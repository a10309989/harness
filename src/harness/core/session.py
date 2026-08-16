"""SessionManager — session lifecycle management with SQLite persistence.

Sessions and conversation turns are persisted to SQLite, surviving server restarts.
Active sessions are cached in memory for fast access, with writes going to SQLite.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from harness.db.protocols import DatabaseProtocol
from harness.models.session import ConversationTurn, Session, SessionState
from harness.utils.id_gen import generate_id

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages user session lifecycle with SQLite persistence.

    Active sessions are cached in memory for fast reads.
    All mutations (create, add_turn, end) write through to SQLite.
    On startup, unexpired sessions are loaded from SQLite into memory.
    """

    def __init__(
        self,
        db: DatabaseProtocol | None = None,
        session_ttl: timedelta = timedelta(hours=2),
        cleanup_interval: int = 300,
    ) -> None:
        self.db = db
        self._active_sessions: dict[str, Session] = {}
        self._pending_writes: set[asyncio.Task] = set()
        self._session_ttl = session_ttl
        self._cleanup_interval = cleanup_interval

    async def initialize(self) -> None:
        """Load unexpired sessions from SQLite into memory cache."""
        if self.db is None:
            return
        now = self._now()
        rows = await self.db.fetch_all(
            "SELECT * FROM sessions WHERE state != 'completed' AND state != 'expired' AND (expires_at IS NULL OR expires_at > $1)",
            (now,),
        )
        for row in rows:
            session = await self._load_session_from_db(row["id"])
            if session:
                self._active_sessions[session.id] = session
        logger.info(f"SessionManager initialized — loaded {len(self._active_sessions)} session(s) from DB")

    async def _load_session_from_db(self, session_id: str) -> Session | None:
        """Load a session and its conversation turns from SQLite."""
        if self.db is None:
            return None
        row = await self.db.fetch_one(
            "SELECT * FROM sessions WHERE id = $1",
            (session_id,),
        )
        if not row:
            return None

        # Load conversation turns
        turn_rows = await self.db.fetch_all(
            "SELECT * FROM conversation_turns WHERE session_id = $1 ORDER BY timestamp",
            (session_id,),
        )
        turns = [
            ConversationTurn(
                role=r["role"],
                content=r["content"],
                timestamp=self._datetime_from_db(r["timestamp"]) if r["timestamp"] else datetime.now(timezone.utc),
                metadata=json.loads(r["metadata"]) if r["metadata"] else {},
            )
            for r in turn_rows
        ]

        context_data = json.loads(row["context_data"]) if row["context_data"] else {}

        return Session(
            id=row["id"],
            user_id=row["user_id"],
            tenant_id=row["tenant_id"],
            state=SessionState(row["state"]),
            conversation=turns,
            current_agent=row["current_agent"],
            context_data=context_data,
            expires_at=self._datetime_from_db(row["expires_at"]) if row["expires_at"] else None,
            last_activity=self._datetime_from_db(row["last_activity"]) if row["last_activity"] else None,
            deleted_by=row["deleted_by"] if "deleted_by" in row.keys() else None,
            deleted_at=self._datetime_from_db(row["deleted_at"]) if "deleted_at" in row.keys() and row["deleted_at"] else None,
        )

    def create_session(
        self,
        user_id: str = "default",
        tenant_id: str = "default",
        metadata: dict | None = None,
    ) -> Session:
        """Create a new session (sync wrapper — DB write happens in background)."""
        session = Session(
            id=generate_id(),
            user_id=user_id,
            tenant_id=tenant_id,
            state=SessionState.CREATED,
            context_data=metadata or {},
        )
        session.touch()
        self._active_sessions[session.id] = session
        # Schedule DB write
        if self.db is not None:
            self._schedule_write(self._persist_session(session))
        logger.info(f"Session created: {session.id}")
        return session

    async def _persist_session(self, session: Session) -> None:
        """Write session to SQLite."""
        if self.db is None:
            return
        now = self._now()
        await self.db.execute(
            """INSERT INTO sessions (id, user_id, tenant_id, state, current_agent,
                                          context_data, expires_at, last_activity, deleted_by, deleted_at,
                                          created_at, updated_at)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                   ON CONFLICT(id) DO UPDATE SET
                       user_id=excluded.user_id, tenant_id=excluded.tenant_id,
                       state=excluded.state, current_agent=excluded.current_agent,
                       context_data=excluded.context_data, expires_at=excluded.expires_at,
                       last_activity=excluded.last_activity, deleted_by=excluded.deleted_by,
                       deleted_at=excluded.deleted_at, updated_at=excluded.updated_at""",
            (
                session.id,
                session.user_id,
                session.tenant_id,
                str(session.state),
                session.current_agent,
                json.dumps(session.context_data, ensure_ascii=False),
                self._datetime_to_db(session.expires_at) if session.expires_at else None,
                self._datetime_to_db(session.last_activity) if session.last_activity else None,
                session.deleted_by,
                self._datetime_to_db(session.deleted_at) if session.deleted_at else None,
                now,
                now,
            ),
        )
        await self.db.commit()

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by ID from memory cache."""
        session = self._active_sessions.get(session_id)
        if session:
            session.touch()
            # Schedule DB update for the touch
            if self.db is not None:
                self._schedule_write(self._persist_session(session))
        return session

    async def get_session_or_load(self, session_id: str) -> Session | None:
        """Retrieve a session from memory cache or durable storage."""
        session = self.get_session(session_id)
        if session is not None:
            return session
        session = await self._load_session_from_db(session_id)
        if session and session.deleted_at is None and session.state not in (SessionState.COMPLETED, SessionState.EXPIRED):
            self._active_sessions[session.id] = session
        return session

    async def reload_session(self, session_id: str) -> Session | None:
        """Refresh one session from durable storage for cross-process writers."""
        if self.db is None:
            return self._active_sessions.get(session_id)
        session = await self._load_session_from_db(session_id)
        if session is not None:
            self._active_sessions[session.id] = session
        return session

    async def list_sessions(
        self,
        tenant_id: str = "default",
        user_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List non-deleted sessions as lightweight archive records."""
        if self.db is None:
            sessions = [
                session for session in self._active_sessions.values()
                if session.tenant_id == tenant_id and (user_id is None or session.user_id == user_id)
            ]
            ordered = sorted(sessions, key=lambda item: item.last_activity or item.updated_at, reverse=True)
            return [self._session_summary(session) for session in ordered[:limit]]

        if user_id:
            sql = """SELECT s.*, COUNT(t.id) AS turn_count,
                          (array_agg(t.content ORDER BY t.timestamp)
                           FILTER (WHERE t.role = 'user'))[1] AS title
                   FROM sessions s
                   LEFT JOIN conversation_turns t ON t.session_id = s.id
                   WHERE s.tenant_id = $1 AND s.user_id = $2 AND s.deleted_at IS NULL
                   GROUP BY s.id
                   ORDER BY COALESCE(s.last_activity, s.updated_at, s.created_at) DESC
                   LIMIT $3"""
            params = (tenant_id, user_id, limit)
        else:
            sql = """SELECT s.*, COUNT(t.id) AS turn_count,
                          (array_agg(t.content ORDER BY t.timestamp)
                           FILTER (WHERE t.role = 'user'))[1] AS title
                   FROM sessions s
                   LEFT JOIN conversation_turns t ON t.session_id = s.id
                   WHERE s.tenant_id = $1 AND s.deleted_at IS NULL
                   GROUP BY s.id
                   ORDER BY COALESCE(s.last_activity, s.updated_at, s.created_at) DESC
                   LIMIT $2"""
            params = (tenant_id, limit)
        rows = await self.db.fetch_all(sql, params)
        return [self._row_summary(row) for row in rows]

    def activate_session(self, session_id: str) -> Session:
        """Mark a session as active."""
        session = self._active_sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        session.state = SessionState.ACTIVE
        session.touch()
        if self.db is not None:
            self._schedule_write(self._persist_session(session))
        return session

    def end_session(self, session_id: str) -> Session:
        """End and archive a session."""
        session = self._active_sessions.pop(session_id, None)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        session.state = SessionState.COMPLETED
        if self.db is not None:
            self._schedule_write(self._persist_session(session))
        logger.info(f"Session ended: {session_id}")
        return session

    async def delete_session(self, session_id: str, deleted_by: str) -> Session:
        """Soft-delete a session from the user's visible history."""
        session = self._active_sessions.pop(session_id, None)
        if session is None:
            session = await self._load_session_from_db(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        session.deleted_by = deleted_by
        session.deleted_at = datetime.now(timezone.utc)
        session.touch()
        if self.db is not None:
            await self._persist_session(session)
        logger.info(f"Session deleted from history: {session_id}")
        return session

    async def update_context(self, session_id: str, values: dict) -> Session:
        """Persist session-scoped product state without creating a chat turn."""
        session = await self.get_session_or_load(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        session.context_data = {**session.context_data, **values}
        session.touch()
        self._active_sessions[session.id] = session
        await self._persist_session(session)
        return session

    async def update_latest_turn_metadata(
        self,
        session_id: str,
        role: str,
        values: dict,
    ) -> None:
        """Merge metadata into the latest matching persisted conversation turn."""
        session = await self.get_session_or_load(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        for turn in reversed(session.conversation):
            if turn.role == role:
                turn.metadata = {**turn.metadata, **values}
                break
        if self.db is None:
            return
        row = await self.db.fetch_one(
            """SELECT id, metadata FROM conversation_turns
               WHERE session_id = $1 AND role = $2
               ORDER BY timestamp DESC, id DESC LIMIT 1""",
            (session_id, role),
        )
        if row is None:
            return
        existing = json.loads(row["metadata"]) if row["metadata"] else {}
        await self.db.execute(
            "UPDATE conversation_turns SET metadata = $1 WHERE id = $2",
            (json.dumps({**existing, **values}, ensure_ascii=False), row["id"]),
        )
        await self.db.commit()

    async def replace_requirements_review_metadata(
        self,
        session_id: str,
        review_id: str,
        review: dict,
        *,
        anchor_latest: bool = False,
    ) -> None:
        """Update every persisted message that references one requirements review."""
        session = await self.get_session_or_load(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        matching_turns = []
        for turn in session.conversation:
            current = turn.metadata.get("requirements_review") if turn.metadata else None
            if isinstance(current, dict) and current.get("review_id") == review_id:
                matching_turns.append(turn)
        for index, turn in enumerate(matching_turns):
            turn.metadata = {
                **turn.metadata,
                "requirements_review": review,
                "requirements_review_anchor": anchor_latest and index == len(matching_turns) - 1,
            }
        if self.db is None:
            return
        rows = await self.db.fetch_all(
            """SELECT id, metadata FROM conversation_turns
               WHERE session_id = $1 AND role = $2
               ORDER BY timestamp ASC, id ASC""",
            (session_id, "assistant"),
        )
        matching_rows = []
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            current = metadata.get("requirements_review")
            if isinstance(current, dict) and current.get("review_id") == review_id:
                matching_rows.append((row["id"], metadata))
        for index, (turn_id, metadata) in enumerate(matching_rows):
            metadata["requirements_review"] = review
            metadata["requirements_review_anchor"] = anchor_latest and index == len(matching_rows) - 1
            await self.db.execute(
                "UPDATE conversation_turns SET metadata = $1 WHERE id = $2",
                (json.dumps(metadata, ensure_ascii=False), turn_id),
            )
        await self.db.commit()

    def add_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Add a conversation turn to a session."""
        session = self.get_session(session_id)
        if session is None:
            return
        turn = ConversationTurn(
            role=role,
            content=content,
            metadata=metadata or {},
            timestamp=datetime.now(timezone.utc),
        )
        session.conversation.append(turn)
        # Persist the turn to SQLite
        if self.db is not None:
            self._schedule_write(self._persist_turn(session_id, turn))

    async def add_turn_durable(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> ConversationTurn:
        """Add a turn and wait until it is durable.

        Use this for state-transition messages whose metadata is read by a
        later request.  The ordinary chat path remains asynchronous, while a
        requirements-review confirmation cannot race a queued draft insert.
        """
        session = self._active_sessions.get(session_id)
        if session is None:
            session = await self._load_session_from_db(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")

        turn = ConversationTurn(
            role=role,
            content=content,
            metadata=metadata or {},
            timestamp=datetime.now(timezone.utc),
        )
        session.conversation.append(turn)
        session.touch()
        self._active_sessions[session.id] = session
        if self.db is not None:
            await self._persist_session(session)
            await self._persist_turn(session_id, turn)
        return turn

    async def _persist_turn(self, session_id: str, turn: ConversationTurn) -> None:
        """Write a conversation turn to SQLite."""
        if self.db is None:
            return
        await self.db.execute(
            "INSERT INTO conversation_turns (session_id, role, content, metadata, timestamp) VALUES ($1, $2, $3, $4, $5)",
            (
                session_id,
                turn.role,
                turn.content,
                json.dumps(turn.metadata, ensure_ascii=False) if turn.metadata else None,
                self._datetime_to_db(turn.timestamp),
            ),
        )
        await self.db.commit()

    async def cleanup_expired(self) -> int:
        """Remove expired sessions."""
        now = datetime.now(timezone.utc)
        now_db = self._datetime_to_db(now)
        expired_ids = [
            sid for sid, s in self._active_sessions.items()
            if s.expires_at and s.expires_at < now
        ]
        for sid in expired_ids:
            session = self._active_sessions.pop(sid)
            session.state = SessionState.EXPIRED
            await self._persist_session(session)
            logger.info(f"Session expired: {sid}")

        # Also clean up from DB
        if self.db is not None:
            await self.db.execute(
                "UPDATE sessions SET state = 'expired', updated_at = $1 WHERE expires_at IS NOT NULL AND expires_at < $2 AND state NOT IN ('completed', 'expired')",
                (now_db, now_db),
            )
            await self.db.commit()
        return len(expired_ids)

    async def cleanup_loop(self) -> None:
        """Background task that periodically cleans up expired sessions."""
        while True:
            await asyncio.sleep(self._cleanup_interval)
            await self.cleanup_expired()

    async def close_all(self) -> None:
        """Flush active sessions without changing their durable lifecycle state."""
        for session in list(self._active_sessions.values()):
            await self._persist_session(session)
        if self._pending_writes:
            await asyncio.gather(*tuple(self._pending_writes), return_exceptions=True)
        self._active_sessions.clear()

    def _schedule_write(self, awaitable) -> None:
        task = asyncio.create_task(awaitable)
        self._pending_writes.add(task)
        task.add_done_callback(self._pending_writes.discard)

    def _now(self):
        return self._datetime_to_db(datetime.now(timezone.utc))

    def _session_summary(self, session: Session) -> dict:
        first_user_turn = next((turn for turn in session.conversation if turn.role == "user"), None)
        title = first_user_turn.content if first_user_turn else "新会话"
        return {
            "session_id": session.id,
            "state": str(session.state),
            "title": title[:80],
            "turn_count": len(session.conversation),
            "last_activity": session.last_activity.isoformat() if session.last_activity else None,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        }

    def _row_summary(self, row) -> dict:
        title = row["title"] or "新会话"
        created_at = self._datetime_from_db(row["created_at"]) if row["created_at"] else None
        updated_at = self._datetime_from_db(row["updated_at"]) if row["updated_at"] else None
        last_activity = self._datetime_from_db(row["last_activity"]) if row["last_activity"] else updated_at
        return {
            "session_id": row["id"],
            "state": row["state"],
            "title": title[:80],
            "turn_count": row["turn_count"] or 0,
            "last_activity": last_activity.isoformat() if last_activity else None,
            "created_at": created_at.isoformat() if created_at else None,
            "updated_at": updated_at.isoformat() if updated_at else None,
        }

    def _datetime_to_db(self, value: datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _datetime_from_db(value) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        return datetime.fromisoformat(value)
