import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from spxlab.events import EventStore,atomic_json

spec=importlib.util.spec_from_file_location('resume_capture','scripts/resume_capture.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class RecoveryGuards(unittest.TestCase):
    def setup_run(self,root):
        source=root/'implementation/src/spxlab/collector.py'
        source.parent.mkdir(parents=True);source.write_text('# fixture frozen collector\n')
        atomic_json(root/'plan.json',{'source_manifest':{'src/spxlab/collector.py':hashlib.sha256(source.read_bytes()).hexdigest()}})
        for name in ('decision.json','control-decision.json'):atomic_json(root/name,{'done':True})
        store=EventStore(root/'events.sqlite');store.append('RUN_STOPPED',{},run_id='prior',generation=6);store.close()
        return source

    def test_unfinished_main_or_control_is_rejected(self):
        for name in ('decision.json','control-decision.json'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                root=Path(d);self.setup_run(root);atomic_json(root/name,{'done':False})
                with self.assertRaises(ValueError):m.inspect_run(root)

    def test_modified_frozen_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=self.setup_run(root);source.write_text('# altered\n')
            with self.assertRaises(ValueError):m.inspect_run(root)

    def test_inspection_preserves_files_and_returns_last_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.setup_run(root)
            before={name:(root/name).read_bytes() for name in ('plan.json','decision.json','control-decision.json')}
            _,protected,seq,generation=m.inspect_run(root)
            self.assertEqual((seq,generation),(1,6))
            for name,data in before.items():
                self.assertEqual((root/name).read_bytes(),data)
                self.assertEqual(protected[name],hashlib.sha256(data).hexdigest())


if __name__=='__main__':unittest.main()
