from pm_agent.governance import AutonomyLevel
from pm_agent.playbooks import PRINCIPLES, get_playbook, load_playbooks
from pm_agent.schemas.project import DEFAULT_PLAYBOOKS


def test_playbooks_load_and_match_the_default_profile():
    playbooks = load_playbooks()
    assert set(playbooks) == set(DEFAULT_PLAYBOOKS)


def test_playbook_fields():
    for playbook in load_playbooks().values():
        assert playbook.standard == "PMBOK8"
        assert set(playbook.principles) <= set(PRINCIPLES)
        assert isinstance(playbook.autonomy, AutonomyLevel)
        assert playbook.instructions.strip()


def test_render_includes_guardrails_and_principles():
    text = get_playbook("pmbok8.risk.identify_and_analyze_risks").render("demo")
    assert "Autonomy: L3" in text
    assert "Identify Risks" in text
    assert "Adopt a Holistic View" in text
    assert "Never compute or choose them yourself" in text
