# ============================================================================
# 2026-09-03: 여기 있던 daily_collector_schedule(EventBridge rule/target/
# permission, 매시 30분에 daily_search_collector Lambda를 직접 트리거)을
# 제거했다. infra/pipeline_orchestrator.tf의 pipeline_orchestrator 상태머신이
# 같은 스케줄식(var.daily_collector_schedule_expression)으로 매시 30분에
# 시작하면서, 그 상태머신의 첫 Task(BronzeCollect)가 이 Lambda를 동기 호출한다.
#
# daily_search_collector Lambda 리소스 자체(infra/lambda.tf)는 그대로다 - 이제
# EventBridge가 아니라 Step Functions가 호출할 뿐. Silver 완료 전에 다음 Bronze
# 회차가 겹쳐 시작되는 걸 막기 위해서라도(각 단계가 완전히 끝나야 다음 단계로
# 넘어가야 한다는 요구사항), Bronze를 더 이상 독립적으로 스케줄하지 않는다.
# ============================================================================
