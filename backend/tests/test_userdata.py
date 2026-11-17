"""The agents' read-only tools over the user's own goals, saved plans, calendar and past conversations."""

from datetime import date

from nip.agents import userdata
from nip.agents.specs import known_numbers, ungrounded_numbers
from nip.api.store import Store


async def test_user_data_tools_are_scoped_to_the_user_and_ground_numbers():
    store = Store()
    userdata.set_store(store)
    try:
        await store.add("u1", "goal", {"kind": "weight", "title": "Reach 65 kg", "target": 65, "unit": "kg", "current": 60})
        await store.add("u2", "goal", {"kind": "habit", "title": "Other user's goal"})
        plan = await store.add("u1", "plan", {"kind": "meal", "title": "Gain plan", "data": {"days": [{"day": 1, "totals": {
            "kcal": 3016, "protein_g": 127}, "meals": [{"slot": "lunch", "name": "Rajma rice",
                                                         "items": [{"food_name": "Rajmah", "grams": 120}]}]}]}})
        await store.add("u1", "calendar", {"date": date.today().isoformat(), "kind": "meal", "title": "Rajma rice",
                                           "plan_title": "Gain plan", "kcal": 3016, "done": False})
        ctx = {"user_id": "u1"}
        tools = {t.name: t for t in userdata.user_data_tools(ctx)}
        goals = await tools["my_goals"].ainvoke({})
        assert [g["title"] for g in goals["goals"]] == ["Reach 65 kg"]  # not u2's goal
        plans = await tools["my_saved_plans"].ainvoke({})
        assert plans["plans"][0]["kcal_per_day"] == [3016]
        detail = await tools["my_saved_plan"].ainvoke({"plan_id": plan["id"]})
        assert detail["days"][0]["meals"][0]["items"] == ["Rajmah 120 g"]
        cal = await tools["my_calendar"].ainvoke({})
        assert cal["entries"][0]["title"] == "Rajma rice"
        assert (await tools["my_saved_plan"].ainvoke({"plan_id": "nope"}))["error"]
        # what the tools returned counts as context for the answer's number check
        assert ungrounded_numbers("Your plan gives 3016 kcal toward 65 kg.", known_numbers(ctx)) == []
        assert await userdata.inventory("u1") == {"goals": 1, "saved_plans": 1, "calendar_next_7_days": 1,
                                                  "past_conversations": 0}
        other = {t.name: t for t in userdata.user_data_tools({"user_id": "u2"})}
        assert (await other["my_saved_plans"].ainvoke({}))["plans"] == []
    finally:
        userdata.set_store(None)


async def test_tools_say_when_no_user_data_is_available():
    userdata.set_store(None)
    tools = {t.name: t for t in userdata.user_data_tools({"user_id": "u1"})}
    assert "not available" in (await tools["my_goals"].ainvoke({}))["error"]


async def test_single_read_my_data_tool_covers_every_lookup():
    store = Store()
    userdata.set_store(store)
    try:
        await store.add("u1", "goal", {"kind": "weight", "title": "Reach 65 kg", "target": 65})
        tool = userdata.read_my_data_tool({"user_id": "u1"})
        assert (await tool.ainvoke({"what": "goals"}))["goals"][0]["title"] == "Reach 65 kg"
        assert (await tool.ainvoke({"what": "calendar"}))["entries"] == []
        assert "plan_id" in (await tool.ainvoke({"what": "saved_plan"}))["error"]
    finally:
        userdata.set_store(None)
