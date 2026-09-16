"""FIELD M1: causal minute facts and a deliberately uncalibrated Gaussian mixture.

Units are index points and minutes. A mixture component is a persistent latent
process over the forecast horizon, not a fresh jump coin at every time step.
"""
from copy import deepcopy
from datetime import timedelta
import math

from .contracts import digest, require, stamp
from .forecast import anchor_value

VERSION = 'FIELD_LOCAL_MIXTURE_V1'
ROOT2 = math.sqrt(2)
ROOT2PI = math.sqrt(2*math.pi)


def normal_call(mean, sd, strike):
    if sd <= 1e-10:
        return max(mean-strike, 0.)
    z = (mean-strike)/sd
    return (mean-strike)*.5*math.erfc(-z/ROOT2)+sd*math.exp(-.5*z*z)/ROOT2PI


def normal_butterfly(mean, sd, center, width):
    value = normal_call(mean, sd, center-width)-2*normal_call(mean, sd, center)+normal_call(mean, sd, center+width)
    return min(float(width), max(0., value))


def transition(component, spot, minutes):
    require(minutes >= 0, 'Negative process horizon')
    k, q = component['kappa'], component['q']
    if k < 1e-10:
        return spot, q*minutes
    return (component['anchor']+(spot-component['anchor'])*math.exp(-k*minutes),
            q*(-math.expm1(-2*k*minutes))/(2*k))


def marginal(process, minutes=None):
    minutes = process['minutes'] if minutes is None else minutes
    result = []
    for c in process['components']:
        mean, var = transition(c, process['spot'], minutes)
        result.append({'weight':c['weight'], 'mean':mean, 'sd':math.sqrt(var)})
    return result


def payoff(mixture, center, width=25):
    return sum(c['weight']*normal_butterfly(c['mean'], c['sd'], float(center), float(width)) for c in mixture)


def cdf(mixture, value):
    return sum(c['weight']*(float(value >= c['mean']) if c['sd'] <= 1e-10 else
               .5*math.erfc((c['mean']-value)/(ROOT2*c['sd']))) for c in mixture)


def quantile(mixture, probability):
    lo = min(c['mean']-12*max(c['sd'],1e-6) for c in mixture)
    hi = max(c['mean']+12*max(c['sd'],1e-6) for c in mixture)
    # This brackets a quantile; it does not truncate/renormalize the distribution.
    for _ in range(65):
        mid = (lo+hi)/2
        if cdf(mixture,mid) < probability: lo = mid
        else: hi = mid
    return (lo+hi)/2


def condition(process, future_spot, delta):
    """Bayesian latent-component update under the SAME frozen process."""
    require(0 < delta < process['minutes'], 'Conditional step must precede terminal target')
    logweights = []
    for c in process['components']:
        mean, variance = transition(c, process['spot'], delta)
        require(variance > 0, 'Degenerate conditional transition')
        logweights.append(math.log(c['weight'])-.5*(math.log(variance)+(future_spot-mean)**2/variance))
    largest = max(logweights)
    weights = [math.exp(x-largest) for x in logweights]
    total = sum(weights)
    out = deepcopy(process)
    out.update(spot=future_spot, minutes=process['minutes']-delta)
    for c,w in zip(out['components'],weights): c['weight'] = w/total
    return out


class MinuteTape:
    """Only actual qualified index prints; no fill-forward or cross-gap returns."""
    def __init__(self, schedule):
        self.schedule = schedule
        self.bars = []
        self.current = None
        self.segment = 0
        self.gaps = []
        self.last_seq = None

    def break_path(self, at, reason):
        self.segment += 1
        self.current = None
        self.gaps.append({'at':at,'reason':reason})

    def advance(self, at):
        if self.current and stamp(at) >= stamp(self.current['end_at']):
            self.bars.append(self.current)
            self.current = None

    def add(self, ref):
        if ref['seq'] == self.last_seq: return
        self.last_seq = ref['seq']
        t = stamp(ref['utc'])
        if not stamp(self.schedule['open_utc']) <= t < stamp(self.schedule['close_utc']): return
        self.advance(ref['utc'])
        start = t.replace(second=0,microsecond=0)
        end = start+timedelta(minutes=1)
        # Closed bars are immutable, including after a reconnect. A delayed
        # observation must never create a second bar for an already closed minute.
        if self.bars and start < stamp(self.bars[-1]['end_at']): return
        if self.current and t < stamp(self.current['last_at']): return
        if self.current is None:
            if self.bars and stamp(self.bars[-1]['end_at']) < start and self.bars[-1]['segment'] == self.segment:
                self.gaps.append({'at':start.isoformat(),'reason':'MISSING_MINUTES',
                                  'previous_end':self.bars[-1]['end_at']})
                self.segment += 1
            self.current = {'start_at':start.isoformat(),'end_at':end.isoformat(),
                'open':ref['value'],'high':ref['value'],'low':ref['value'],'close':ref['value'],
                'first_at':ref['utc'],'last_at':ref['utc'],'first_seq':ref['seq'],
                'last_seq':ref['seq'],'observations':0,'segment':self.segment}
        b = self.current
        b.update(close=ref['value'],last_at=ref['utc'],last_seq=ref['seq'],
                 observations=b['observations']+1,high=max(b['high'],ref['value']),low=min(b['low'],ref['value']))

    def increments(self, at):
        self.advance(at)
        contiguous = []
        for a,b in zip(self.bars,self.bars[1:]):
            # Minute closes need a real print in the last five seconds. A lost
            # segment or a missing minute resets the local estimation window.
            valid = (a['end_at'] == b['start_at'] and a['segment'] == b['segment'] == self.segment and
                     (stamp(a['end_at'])-stamp(a['last_at'])).total_seconds() <= 5 and
                     (stamp(b['end_at'])-stamp(b['last_at'])).total_seconds() <= 5)
            if valid: contiguous.append((a,b))
            else: contiguous = []
        return contiguous[-60:]


def estimate(tape, at, anchor=None):
    pairs = tape.increments(at)
    result = {'model_version':VERSION, 'n_increments':len(pairs), 'anchor':anchor,
              'parameter_cutoff':pairs[-1][1]['end_at'] if pairs else None,
              'training_data':'CURRENT_SESSION_CONTIGUOUS_MINUTES', 'issues':[]}
    if len(pairs) < 20:
        return {**result,'status':'WARMUP'}
    changes = [b['close']-a['close'] for a,b in pairs]
    decay = math.exp(-math.log(2)/20)
    weights = [decay**i for i in reversed(range(len(changes)))]
    q = sum(w*d*d for w,d in zip(weights,changes))/sum(weights)
    # No jump removal, no optimistic volatility floor. A zero/implausible scale
    # is an explicit model fault rather than a certain zero-risk prediction.
    if not math.isfinite(q) or not 1e-8 < q < 10000:
        return {**result,'status':'MODEL_FAULT','issues':['NOISE_OUTSIDE_MODEL_DOMAIN'],'q':q}
    raw = 0.
    if anchor is not None:
        xs = [a['close']-anchor for a,b in pairs]
        den = sum(x*x for x in xs)
        if den <= 1e-8: result['issues'].append('WEAK_KAPPA_IDENTIFICATION')
        else: raw = -sum(x*d for x,d in zip(xs,changes))/den
    shrunk = len(changes)/(len(changes)+60)*raw
    used = max(0.,min(shrunk,math.log(2)/10))
    if shrunk > used: result['issues'].append('POSITIVE_KAPPA_CAPPED')
    return {**result,'status':'READY','q':q,'kappa_raw':raw,'kappa_shrunk':shrunk,'kappa_used':used,
            'parameter_input_hash':digest(pairs),'parameter_last_seq':pairs[-1][1]['last_seq']}


def make_process(parameters, spot, minutes, *, anchor_shift=0., noise_scale=1.):
    anchor, k = parameters['anchor'],parameters['kappa_used']
    anchored = anchor is not None and k > 0
    branches = [('BACKGROUND',1. if not anchored else .5,0.,spot)]
    if anchored: branches.append(('ANCHORED',.5,k,anchor+anchor_shift))
    components = []
    for name,weight,kappa,level in branches:
        for label,prob,scale in [('BASE',.9,1.),('WIDE',.1,9.)]:
            components.append({'name':name+'_'+label,'weight':weight*prob,'kappa':kappa,
                               'anchor':level,'q':parameters['q']*scale*noise_scale})
    return {'spot':float(spot),'minutes':minutes,'components':components}


def build_model(tape, spot, at, target, source, *, computed_at, algorithm_hash, previous_pin=None):
    anchor = float(anchor_value(source)) if source else None
    parameters = estimate(tape, at, anchor)
    minutes = (stamp(target)-stamp(at)).total_seconds()/60
    status = parameters['status']
    diagnostic = {**parameters,'state':'WARMUP' if status == 'WARMUP' else 'MIXED',
        'anchor_mode':'PIN' if source else 'MARKET_ONLY','pin':anchor,
        'pin_change':anchor-previous_pin if anchor is not None and previous_pin is not None else None,
        'source_hash':source.get('content_hash') if source else None,'remaining_minutes':minutes,
        'pin_source':({k:source.get(k) for k in ('forecast_id','source_role','statistic_type','first_seen_at','available_at','model_version')} if source else None),
        'spot':spot['value'] if spot else None,'spot_at':spot['utc'] if spot else None,
        'assumptions':['未校准算术正态混合','宽噪声10%为模型先验，非实测跳跃概率',
                       '背景/锚定各半是模型权重，非收敛概率']}
    if not spot or status != 'READY' or minutes <= 0:
        return None,diagnostic
    if (stamp(at)-stamp(parameters['parameter_cutoff'])).total_seconds() > 90:
        diagnostic.update(status='STALE_PARAMETERS',issues=['PARAMETERS_OLDER_THAN_90_SECONDS'])
        return None,diagnostic
    process = make_process(parameters,spot['value'],minutes)
    mixture = marginal(process)
    if any(c['sd'] > spot['value']*.25 or c['mean'] <= 0 for c in mixture):
        diagnostic.update(status='MODEL_FAULT',issues=['TERMINAL_SCALE_OUTSIDE_MODEL_DOMAIN'])
        return None,diagnostic
    kt = parameters['kappa_shrunk']*minutes
    state = ('CONVERGING_CANDIDATE' if anchor is not None and kt >= .25 else
             'EXPANDING_CANDIDATE' if anchor is not None and kt < 0 else 'MIXED')
    diagnostic.update(state=state,kappa_tau=kt,distance_to_pin=spot['value']-anchor if anchor is not None else None,
        interval80=[quantile(mixture,.1),quantile(mixture,.9)],
        background_sd=math.sqrt(parameters['q']*minutes),
        anchored_sd=math.sqrt(transition({'kappa':parameters['kappa_used'],'q':parameters['q'],
            'anchor':anchor or spot['value']},spot['value'],minutes)[1]))
    record = {'schema_version':2,'model_version':VERSION,'measure':'P_ESTIMATE',
        'representation':'NORMAL_MIXTURE','components':mixture,'process':process,'parameters':parameters,
        'target_at':target,'information_cutoff':at,'conditioning_as_of':at,'computed_at':computed_at,
        'available_at':computed_at,'training_cutoff':parameters['parameter_cutoff'],
        'calibration_status':'EXPLORATORY','provenance':{'algorithm_hash':algorithm_hash,
            'market_snapshot_seq':spot['seq'],'spot_observed_at':spot['utc'],
            'source_hash':diagnostic['source_hash'],'classification':'MARKET_OBSERVATION'},
        'diagnostic':diagnostic}
    return record,diagnostic
