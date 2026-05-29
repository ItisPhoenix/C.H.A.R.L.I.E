import logging
import os
import time
from typing import List

from charlie.intelligence.memory_graph import MemoryGraph
from charlie.memory.memory_coordinator import MemoryCoordinator

logger = logging.getLogger("charlie.intelligence.graph_builder")

class GraphBuilder:
    """
    GraphBuilder: Automated ingestion for MemoryGraph.
    Pulls from files, browser history, and git logs.
    """
    def __init__(self, graph: MemoryGraph, memory: MemoryCoordinator):
        self.graph = graph
        self.memory = memory

    def _save_and_index(self, title: str, content: str, tags: List[str]):
        """Saves to MD file AND indexes into vector DB."""
        self.graph.save_node(title, content, tags)
        self.memory.store_fact(
            subject=title,
            predicate="documented_in",
            obj=f"TAGS: {tags}\n\n{content}",
            source="graph_builder",
        )

    def run_full_index(self):
        """Executes all ingestion pipelines."""
        logger.info("graph_builder | starting_full_index")
        self._index_recent_files()
        self._index_git_logs()
        # Browser indexing is complex/OS-specific, skipped for now

    def _index_recent_files(self):
        """Indexes files modified in the last 24 hours in the workspace."""
        now = time.time()
        for root, _, files in os.walk("."):
            if ".git" in root or "__pycache__" in root: continue
            for f in files:
                path = os.path.join(root, f)
                try:
                    if now - os.path.getmtime(path) < 86400: # 24h
                        if f.endswith((".py", ".md", ".txt", ".json", ".yaml")):
                            with open(path, "r", encoding="utf-8") as file:
                                content = file.read(2000)
                                self._save_and_index(
                                    title=f"File: {f}",
                                    content=f"Path: {path}\n\nContent Fragment:\n{content}",
                                    tags=["workspace", "files"]
                                )
                except Exception: continue

    def _index_git_logs(self):
        """Indexes recent git commits."""
        import subprocess
        try:
            res = subprocess.run(["git", "log", "-n", "10", "--pretty=format:%h - %s (%cr)"], capture_output=True, text=True)
            if res.returncode == 0:
                self._save_and_index(
                    title="Git: Recent Commits",
                    content=res.stdout,
                    tags=["git", "codebase"]
                )
        except Exception as e:
            logger.debug(f"git_index_failed | {e}")

