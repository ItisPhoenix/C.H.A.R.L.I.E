import sqlite3
import logging
from typing import List
from .config import config

logger = logging.getLogger("charlie.memory")

class ResearchMemory:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or config.research_memory_db
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS research_snippets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    source_url TEXT,
                    title TEXT,
                    body TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES research_sessions(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS semantic_knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    topic TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def create_session(self, topic: str) -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO research_sessions (topic) VALUES (?)", (topic,))
            conn.commit()
            return cursor.lastrowid

    def add_snippet(self, session_id: int, url: str, title: str, body: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO research_snippets (session_id, source_url, title, body) VALUES (?, ?, ?, ?)",
                (session_id, url, title, body)
            )
            conn.commit()
    def add_semantic_knowledge(self, topic: str, summary: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO semantic_knowledge (topic, summary) VALUES (?, ?)",
                (topic, summary)
            )
            conn.commit()
            logger.info(f"semantic_knowledge_added | {topic}")

    def get_semantic_knowledge(self, current_topic: str) -> str:
        """Extract keywords and find related semantic summaries."""
        stop_words = {"what", "how", "why", "who", "where", "when", "the", "and", "for", "research", "deep", "dive", "about"}
        keywords = [w.lower() for w in current_topic.split() if w.lower() not in stop_words and len(w) > 2]
        
        if not keywords:
            return ""

        results = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for kw in keywords:
                cursor.execute("SELECT topic, summary FROM semantic_knowledge WHERE topic LIKE ?", (f"%{kw}%",))
                for topic, summary in cursor.fetchall():
                    results.append(f"PAST INSIGHT on {topic}: {summary}")
        
        if not results:
            return ""
            
        return "\nSEMANTIC KNOWLEDGE (Context):\n" + "\n".join(list(set(results))[:5])

    def find_related_sessions(self, current_topic: str) -> List[str]:
        """Find topics with keyword overlap (>2 matching non-stop words)."""
        stop_words = {"what", "how", "why", "who", "where", "when", "the", "and", "for", "research", "deep", "dive", "about"}
        keywords = {w.lower() for w in current_topic.split() if w.lower() not in stop_words and len(w) > 2}
        
        if not keywords:
            return []

        matches = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT topic FROM research_sessions")
            for (saved_topic,) in cursor.fetchall():
                saved_keywords = {w.lower() for w in saved_topic.split() if w.lower() not in stop_words and len(w) > 2}
                overlap = keywords.intersection(saved_keywords)
                if len(overlap) >= 1: # Reduced to 1 for better discovery in small datasets
                    matches.append(saved_topic)
        
        return list(set(matches))

    def get_session_summary(self, topic: str) -> str:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.title, s.body 
                FROM research_snippets s
                JOIN research_sessions rs ON s.session_id = rs.id
                WHERE rs.topic = ?
                LIMIT 3
            """, (topic,))
            rows = cursor.fetchall()
            if not rows:
                return f"No snippets found for {topic}."
            
            summary = [f"Summary for {topic}:"]
            for title, body in rows:
                summary.append(f"- {title}: {body[:200]}...")
            return "\n".join(summary)

# Singleton instance
memory = ResearchMemory()
