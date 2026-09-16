"""Proper sample diagnostics; no invented continuous PIT for discrete models."""
from decimal import Decimal
from .contracts import digest,number,require
from .distribution import expected_payoff,validate_distribution
from .evaluation import location_attribution,sample_crps


def prediction_diagnostics(distribution, outcome, center, width, quantiles=('.05','.5','.95')):
    validate_distribution(distribution)
    require(distribution['representation']=='WEIGHTED_SAMPLES','Full common samples required')
    ordered=sorted(distribution['samples'],key=lambda x:number(x['value']))
    y=number(outcome);qs=[]
    for level in quantiles:
        alpha=number(level);require(0<alpha<1,'Interior quantile required')
        mass=Decimal(0);q=None
        for row in ordered:
            mass+=number(row['weight'])
            if mass>=alpha:
                q=number(row['value']);break
        qs.append({'level':str(alpha),'prediction':str(q),'covered_le':y<=q,
                   'pinball_loss':str((alpha-(1 if y<q else 0))*(y-q))})
    low=sum(number(r['weight']) for r in ordered if number(r['value'])<y)
    high=sum(number(r['weight']) for r in ordered if number(r['value'])<=y)
    from .distribution import butterfly
    expected=expected_payoff(distribution,center,width)['point'];actual=butterfly(y,center,width)
    return {'distribution_hash':digest(distribution),'crps':sample_crps(distribution,y),'quantiles':qs,
            'pit_interval':[str(low),str(high)],'pit_convention':'DISCRETE_CDF_LEFT_RIGHT_INTERVAL',
            'central_interval_width':str(number(qs[-1]['prediction'])-number(qs[0]['prediction'])),
            'payoff_prediction_points':str(expected),'payoff_realized_points':str(actual),
            'payoff_error_points':str(expected-actual),'payoff_squared_error':str((expected-actual)**2),
            'classification':'SINGLE_FORECAST_DIAGNOSTIC_NOT_PNL'}


def reselection_ablation(distribution, reference, universe, costs):
    """Point-estimate selection diagnostic, separate from DV1 bound eligibility."""
    validate_distribution(distribution)
    require(distribution['representation']=='WEIGHTED_SAMPLES','Common samples required')
    from copy import deepcopy
    shifted=deepcopy(distribution);mass=Decimal(0)
    for row in sorted(distribution['samples'],key=lambda x:number(x['value'])):
        mass+=number(row['weight'])
        if mass>=Decimal('.5'):
            median=number(row['value']);break
    for row in shifted['samples']:
        row['value']=str(number(row['value'])-median+number(reference))
    def select(d):
        rows=[{'candidate_id':c['candidate_id'],'edge':expected_payoff(d,c['center'],c['width'])['point']-number(costs[c['candidate_id']])} for c in universe]
        return sorted(rows,key=lambda r:(-r['edge'],r['candidate_id']))[0]
    a,b=select(distribution),select(shifted)
    return {'original_selected':a['candidate_id'],'relocated_selected':b['candidate_id'],
            'original_best_point_edge':str(a['edge']),'relocated_best_point_edge':str(b['edge']),
            'classification':'POINT_ESTIMATE_RESELECTION_ABLATION_NOT_DV1_STRATEGY_OR_HEDGE_RETURN'}
