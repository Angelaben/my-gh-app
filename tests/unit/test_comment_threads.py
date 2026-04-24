from datetime import datetime, timezone
from app.domain.models import Comment
from app.services.comment_service import build_threads, enrich_comments


def _c(id: int, author: str, body: str = "x",
       in_reply_to_id: int | None = None,
       created_at: str = "2026-04-20T10:00:00Z") -> Comment:
    return Comment(id=id, author=author, body=body,
                   in_reply_to_id=in_reply_to_id, created_at=created_at)


_BEFORE = datetime(2026, 4, 20, 9, 0, 0, tzinfo=timezone.utc)   # last_visited_at
_AFTER  = "2026-04-20T10:00:00Z"                                  # created_at > _BEFORE
_BEFORE_STR = "2026-04-20T08:00:00Z"                              # created_at < _BEFORE


class TestBuildThreads:
    def test_single_comment_is_its_own_thread(self):
        c = _c(1, "alice")
        threads = build_threads([c])
        assert list(threads.keys()) == [1]
        assert threads[1] == [c]

    def test_reply_grouped_under_root(self):
        root = _c(1, "alice")
        reply = _c(2, "bob", in_reply_to_id=1)
        threads = build_threads([root, reply])
        assert set(threads.keys()) == {1}
        assert set(c.id for c in threads[1]) == {1, 2}

    def test_two_independent_threads(self):
        c1 = _c(1, "alice")
        c2 = _c(2, "bob", in_reply_to_id=1)
        c3 = _c(3, "carol")
        threads = build_threads([c1, c2, c3])
        assert set(threads.keys()) == {1, 3}
        assert set(c.id for c in threads[1]) == {1, 2}
        assert set(c.id for c in threads[3]) == {3}

    def test_orphan_reply_becomes_own_thread(self):
        # in_reply_to_id points to non-existent parent
        reply = _c(2, "bob", in_reply_to_id=999)
        threads = build_threads([reply])
        assert set(threads.keys()) == {2}

    def test_empty_list(self):
        assert build_threads([]) == {}


class TestEnrichComments:
    def test_new_non_ours_comment_added_to_new_ids(self):
        c = _c(1, "alice", created_at=_AFTER)
        _, new_ids = enrich_comments([c], our_login="me", last_visited_at=_BEFORE)
        assert 1 in new_ids

    def test_old_comment_not_in_new_ids(self):
        c = _c(1, "alice", created_at=_BEFORE_STR)
        _, new_ids = enrich_comments([c], our_login="me", last_visited_at=_BEFORE)
        assert 1 not in new_ids

    def test_our_new_comment_not_in_new_ids(self):
        c = _c(1, "me", created_at=_AFTER)
        _, new_ids = enrich_comments([c], our_login="me", last_visited_at=_BEFORE)
        assert 1 not in new_ids

    def test_is_ours_flag_set(self):
        c = _c(1, "me")
        enriched, _ = enrich_comments([c], our_login="me", last_visited_at=None)
        assert enriched[0].is_ours is True

    def test_is_ours_false_for_others(self):
        c = _c(1, "alice")
        enriched, _ = enrich_comments([c], our_login="me", last_visited_at=None)
        assert enriched[0].is_ours is False

    def test_thread_id_set_to_root(self):
        root = _c(1, "me")
        reply = _c(2, "alice", in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=None)
        by_id = {c.id: c for c in enriched}
        assert by_id[1].thread_id == 1
        assert by_id[2].thread_id == 1

    def test_is_new_reply_when_reply_to_ours_after_visit(self):
        root  = _c(1, "me",    created_at=_BEFORE_STR)
        reply = _c(2, "alice", created_at=_AFTER, in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=_BEFORE)
        by_id = {c.id: c for c in enriched}
        assert by_id[2].is_new_reply is True

    def test_is_new_reply_false_when_reply_is_old(self):
        root  = _c(1, "me",    created_at=_BEFORE_STR)
        reply = _c(2, "alice", created_at=_BEFORE_STR, in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=_BEFORE)
        by_id = {c.id: c for c in enriched}
        assert by_id[2].is_new_reply is False

    def test_has_new_replies_set_on_root(self):
        root  = _c(1, "me",    created_at=_BEFORE_STR)
        reply = _c(2, "alice", created_at=_AFTER, in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=_BEFORE)
        by_id = {c.id: c for c in enriched}
        assert by_id[1].has_new_replies is True
        assert by_id[2].has_new_replies is False

    def test_no_has_new_replies_when_reply_by_us(self):
        root  = _c(1, "me",  created_at=_BEFORE_STR)
        reply = _c(2, "me",  created_at=_AFTER, in_reply_to_id=1)
        enriched, _ = enrich_comments([root, reply], our_login="me", last_visited_at=_BEFORE)
        by_id = {c.id: c for c in enriched}
        assert by_id[1].has_new_replies is False

    def test_no_last_visited_means_nothing_is_new(self):
        c = _c(1, "alice", created_at=_AFTER)
        enriched, new_ids = enrich_comments([c], our_login="me", last_visited_at=None)
        assert new_ids == []
        assert enriched[0].is_new_reply is False
