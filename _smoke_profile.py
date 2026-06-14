import os, sys
sys.path.insert(0, os.getcwd())
from charlie.profile_manager import ProfileManager

pm = ProfileManager('SOUL.md', 'USER.md')
soul = pm.load_soul()
print(f"SOUL.md: {len(soul)} chars")
assert len(soul) > 100, "SOUL.md too short"

user = pm.load_user_profile()
print(f"USER.md: {len(user)} chars")

# Test writes
pm.add_user_fact("loves jazz music", "preferences")
pm.add_user_fact("works at Google", "work")
pm.add_user_fact("lives in Berlin", "location")
pm.add_user_fact("loves jazz music", "preferences")  # duplicate

facts = pm.get_user_facts()
print(f"Facts: {facts}")
assert "loves jazz music" in facts
assert "works at Google" in facts
assert "lives in Berlin" in facts

# Test remove
pm.remove_user_fact("lives in Berlin")
facts_after = pm.get_user_facts()
assert "lives in Berlin" not in facts_after

# Test SOUL.md update
pm.update_soul_section("Preferences", "- Music: Jazz\n- Weather: Rain")
soul2 = pm.load_soul()
assert "Music: Jazz" in soul2

# Cleanup
pm.update_soul_section("Preferences", "- **Music:** Electro-swing and lo-fi beats for concentration.\n- **Weather:** Crisp autumn afternoons over blazing summers.\n- **Work Ethos:** Deep work in short bursts. Multitasking is a myth and a trap.\n- **Conversation:** Direct and substance-first. No small talk.\n- **Humor:** Dry, dark, observational.")

# Clean USER.md test artifacts
import re
with open('USER.md') as f:
    content = f.read()
content = re.sub(r'\n- loves jazz music\n', '\n', content)
content = re.sub(r'\n- works at Google\n', '\n', content)
with open('USER.md', 'w') as f:
    f.write(content)

print("All smoke tests: OK")
