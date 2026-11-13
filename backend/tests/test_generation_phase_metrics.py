from app.generation_monitor import timing_summary


def test_wait_model_and_verification_are_measured_independently():
    result=timing_summary([
        {'timing':{'route':'rules','total_seconds':.5,'model_seconds':0}},
        {'timing':{'route':'model','total_seconds':40,'model_seconds':12},
         'owner_rules':{'verification_seconds':25}},
    ],[{'status':'completed','result':{'timing':{'queue_seconds':60}}}])
    assert result['median_model_seconds']==12 and result['model_samples']==1
    assert result['median_verification_seconds']==25 and result['verification_samples']==1
    assert result['median_queue_seconds']==60
    assert result['failed_jobs']==0


def test_unmeasured_phases_are_unknown_not_instantaneous():
    result=timing_summary([{'timing':{'route':'rules','total_seconds':.5}}],[])
    assert result['median_model_seconds'] is None
    assert result['median_verification_seconds'] is None
