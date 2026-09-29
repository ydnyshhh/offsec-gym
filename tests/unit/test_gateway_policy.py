from uuid import uuid4

from offsecgym.gateway.policy import path_allowed, scope_reason
from offsecgym.schemas.actions import ActionRequest
from offsecgym.schemas.domain import ExperimentContext
from offsecgym.schemas.specs import Budget


def test_path_policy_rejects_external_and_ambiguous_forms() -> None:
    assert path_allowed("/hello")
    assert path_allowed("/hello?name=test")
    for path in (
        "http://outside.invalid/",
        "//outside.invalid/",
        "//[",
        "/hello#fragment",
        "/hello\r\nX:y",
    ):
        assert not path_allowed(path)


def test_scope_policy_binds_run_service_and_method() -> None:
    run_id = uuid4()
    context = ExperimentContext(run_id=run_id, range_id=uuid4(), budget=Budget(max_actions=10))
    allowed = ActionRequest(
        run_id=run_id, kind="http_request", destination="hello", method="GET", path="/hello"
    )
    assert scope_reason(allowed, context) is None
    assert scope_reason(allowed.model_copy(update={"run_id": uuid4()}), context) == "run_mismatch"
    assert (
        scope_reason(allowed.model_copy(update={"destination": "outside"}), context)
        == "destination_out_of_scope"
    )
    assert (
        scope_reason(allowed.model_copy(update={"method": "POST"}), context) == "method_not_allowed"
    )


def test_saas_policy_blocks_direct_login_and_unassigned_identity() -> None:
    run_id = uuid4()
    assigned = uuid4()
    context = ExperimentContext(
        run_id=run_id,
        range_id=uuid4(),
        budget=Budget(max_actions=10),
        allowed_identity_ids=(assigned,),
    )
    action = ActionRequest(
        run_id=run_id,
        kind="http_request",
        destination="saas",
        method="GET",
        path="/api/me",
        identity_id=assigned,
    )
    assert scope_reason(action, context, "saas") is None
    assert (
        scope_reason(action.model_copy(update={"identity_id": uuid4()}), context, "saas")
        == "identity_out_of_scope"
    )
    assert (
        scope_reason(action.model_copy(update={"path": "/api/login?next=/api/me"}), context, "saas")
        == "login_is_gateway_managed"
    )
