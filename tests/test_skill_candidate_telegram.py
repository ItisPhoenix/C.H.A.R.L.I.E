import asyncio
from types import SimpleNamespace

import main


class _BrainSpy:
    def __init__(self):
        self.blocks = {}

    def add_installed_skill_block(self, name, block):
        self.blocks[name] = block

    def remove_installed_skill_block(self, name):
        self.blocks.pop(name, None)


class _ExtensionSpy:
    def __init__(self):
        self.content_hash = "a" * 64
        self.blocks = {}
        self.calls = []
        self.candidates = [{
            "name": "reply_style",
            "content_hash": self.content_hash,
            "review_token": "0123456789abcdef",
            "status": "pending",
            "enabled": False,
            "review_submitted": False,
            "matches_hash": True,
            "content": "---\nname: reply_style\n---\nBe concise.",
        }]

    def resolve_skill_candidate_review_token(self, token, expected_status="pending"):
        self.calls.append(("resolve", token, expected_status))
        candidate = self.candidates[0]
        return (
            ("reply_style", self.content_hash)
            if token == candidate["review_token"] and candidate["status"] == expected_status
            else None
        )

    def approve_skill_candidate(self, name, content_hash):
        self.calls.append(("approve", name, content_hash))
        self.candidates[0]["status"] = "approved"
        self.blocks["reply_style"] = "[SKILL: reply_style]\nBe concise."
        return SimpleNamespace(success=True)

    def reject_skill_candidate(self, name, content_hash):
        self.calls.append(("reject", name, content_hash))
        self.candidates[0]["status"] = "rejected"
        return SimpleNamespace(success=True)

    def disable_skill_candidate(self, name, content_hash):
        self.calls.append(("disable", name, content_hash))
        self.candidates[0]["status"] = "disabled"
        self.blocks["reply_style"] = "[SKILL: reply_style]\nUse the previous reply style."
        return SimpleNamespace(success=True)

    def get_active_skill_blocks(self):
        return dict(self.blocks)

    def list_skill_candidates(self):
        return [dict(candidate) for candidate in self.candidates]

    def mark_skill_candidate_review_submitted(self, name, content_hash):
        self.calls.append(("submitted", name, content_hash))
        self.candidates[0]["review_submitted"] = True
        return True


def test_owner_skill_review_approval_uses_persisted_full_hash_and_loads_brain_block():
    async def exercise():
        extensions, brain = _ExtensionSpy(), _BrainSpy()

        accepted = await main._resolve_skill_candidate_review(extensions, brain, "0123456789abcdef", "approve")

        assert accepted is True
        assert ("approve", "reply_style", "a" * 64) in extensions.calls
        assert brain.blocks == {"reply_style": "[SKILL: reply_style]\nBe concise."}

    asyncio.run(exercise())


def test_reject_does_not_activate_or_load_brain_block():
    async def exercise():
        extensions, brain = _ExtensionSpy(), _BrainSpy()

        accepted = await main._resolve_skill_candidate_review(extensions, brain, "0123456789abcdef", "reject")

        assert accepted is True
        assert ("reject", "reply_style", "a" * 64) in extensions.calls
        assert brain.blocks == {}

    asyncio.run(exercise())


def test_disabling_candidate_update_refreshes_brain_with_restored_block():
    async def exercise():
        extensions, brain = _ExtensionSpy(), _BrainSpy()
        extensions.candidates[0]["status"] = "approved"
        brain.blocks["reply_style"] = "[SKILL: reply_style]\nBe concise."

        restored = await main._resolve_skill_candidate_review(
            extensions, brain, "0123456789abcdef", "disable"
        )

        assert restored is True
        assert ("disable", "reply_style", "a" * 64) in extensions.calls
        assert brain.blocks["reply_style"] == "[SKILL: reply_style]\nUse the previous reply style."

    asyncio.run(exercise())


def test_startup_loads_active_skill_blocks_and_submits_pending_review_once():
    async def exercise():
        extensions, brain = _ExtensionSpy(), _BrainSpy()
        extensions.blocks["previous_skill"] = "[SKILL: previous_skill]\nPersisted instructions."
        main._load_rehydrated_skill_blocks(brain, extensions)
        assert brain.blocks == extensions.blocks

        class TelegramSpy:
            def __init__(self):
                self.sent = []

            async def send_skill_candidate_review(self, chat_id, name, content_hash, token, content):
                self.sent.append((chat_id, name, content_hash, token, content))

        telegram = TelegramSpy()
        count = await main._send_pending_skill_candidate_reviews(telegram, extensions, 694903315)
        assert count == 1
        assert telegram.sent == [(
            694903315,
            "reply_style",
            "a" * 64,
            "0123456789abcdef",
            "---\nname: reply_style\n---\nBe concise.",
        )]
        assert extensions.candidates[0]["review_submitted"] is True

        count = await main._send_pending_skill_candidate_reviews(telegram, extensions, 694903315)
        assert count == 0
        assert len(telegram.sent) == 1

    asyncio.run(exercise())


def test_failed_review_send_remains_pending_and_retries_after_next_owner_turn():
    async def exercise():
        extensions = _ExtensionSpy()

        class TelegramSpy:
            calls = 0

            async def send_skill_candidate_review(self, *_args):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary send failure")

        telegram = TelegramSpy()

        assert await main._send_pending_skill_candidate_reviews(telegram, extensions, 42) == 0
        assert extensions.candidates[0]["review_submitted"] is False
        assert await main._send_pending_skill_candidate_reviews(telegram, extensions, 42) == 1
        assert extensions.candidates[0]["review_submitted"] is True

    asyncio.run(exercise())
