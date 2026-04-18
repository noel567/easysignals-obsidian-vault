#!/usr/bin/env python3
"""
Obsidian Auto-Sync Agent
========================

Automatisches Syncing von Chat-Kontext zu Obsidian Vault nach JEDEM Chat.

Funktionalität:
1. Parse last Chat Session → Extract neuer Kontext
2. Klassifiziere: Welche Folder/File gehört dieser Kontext?
   - EasySignals/Products? Processes? Learnings?
   - TeleTrade/API? CRM? Revenue?
   - Organization/Decisions? OKRs?
   - SOPs/Operations?
3. Auto-append zu relevanten .md Files (APPEND ONLY, keine Überschreibungen)
4. Update Knowledge Graph (backlinks neu generieren)
5. Git commit + push

Filter (NICHT synchen wenn):
- Nur Meta-Talk (z.B. "schlafen gehen", "token usage")
- Persönliche Notizen
- Debugging-Gespräche

Voraussetzung:
- pip install frontmatter scikit-learn networkx PyGithub
"""

import os
import json
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Set, Optional
import subprocess
import logging

# NLP imports
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    import networkx as nx
except ImportError:
    print("ERROR: Missing dependencies. Install with:")
    print("pip install scikit-learn networkx")
    sys.exit(1)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# Constants
VAULT_PATH = Path("/tmp/easysignals-obsidian-vault")
WORKSPACE_PATH = Path("/data/.openclaw/workspace")
SCRIPT_DIR = WORKSPACE_PATH / "scripts"
MEMORY_DIR = WORKSPACE_PATH / "memory"
SYNC_LOG = SCRIPT_DIR / "obsidian_auto_sync.log"
SYNC_STATE = SCRIPT_DIR / "obsidian_auto_sync_state.json"
MIN_SIMILARITY_WEIGHT = 0.7
MAX_BACKLINKS_PER_DOC = 5


class ChatContextExtractor:
    """Extrahiere neuen Kontext aus Chat-Session."""

    # Meta-Talk Patterns (NICHT synchen)
    META_PATTERNS = [
        r"(schlafen|sleep|guten nacht|good night|logout)",
        r"(token usage|context|model|api|system|debug)",
        r"(persönliche notizen?|private notes?|personal)",
        r"(testrun|test run|debugging)",
    ]

    # Topic Classifications
    TOPIC_KEYWORDS = {
        "easysignals_products": [
            "signal", "indicator", "product", "feature",
            "cryptocurrency", "trading signal", "alert"
        ],
        "easysignals_learnings": [
            "learned", "insight", "lesson", "best practice",
            "observation", "finding", "improvement", "iterate"
        ],
        "easysignals_processes": [
            "workflow", "process", "pipeline", "procedure",
            "automation", "integration", "setup"
        ],
        "teletrade_api": [
            "api", "endpoint", "webhook", "integration",
            "rest", "authentication", "token"
        ],
        "teletrade_crm": [
            "crm", "customer", "contact", "lead",
            "prospect", "relationship", "pipeline"
        ],
        "teletrade_revenue": [
            "revenue", "income", "pricing", "monetization",
            "subscription", "affiliate", "commission", "payout"
        ],
        "organization_decisions": [
            "decided", "decision", "meeting", "agreed",
            "approved", "approved", "roadmap", "priority"
        ],
        "organization_okrs": [
            "okr", "objective", "key result", "goal",
            "target", "milestone", "kpi", "metric"
        ],
        "sops_operations": [
            "sop", "standard operating", "procedure", "onboarding",
            "documentation", "guide", "instructions", "support"
        ],
        "sops_processes": [
            "workflow", "checklist", "process", "step-by-step",
            "how to", "tutorial", "guide"
        ],
    }

    def __init__(self, memory_dir: Path = MEMORY_DIR):
        self.memory_dir = memory_dir

    def extract_from_latest_chat(self) -> Optional[Dict]:
        """
        Extrahiere neuen Kontext aus letztem Chat.
        
        Returns:
        {
            'raw_content': str,
            'context_summary': str,
            'topics': [str],  # Liste von topics
            'timestamp': str,
            'chat_file': str,
            'should_sync': bool
        }
        """
        logger.info("📖 Extracting context from latest chat...")

        # Finde neueste Memory-Datei
        latest_chat = self._find_latest_memory_file()
        if not latest_chat:
            logger.warning("No recent chat files found")
            return None

        # Lese Chat-Inhalt
        with open(latest_chat, "r", encoding="utf-8") as f:
            content = f.read()

        # Filter: Meta-Talk?
        if self._is_meta_talk(content):
            logger.info("🚫 Chat is meta-talk, skipping...")
            return None

        # Extrahiere Summary
        summary = self._summarize_content(content)
        if not summary:
            logger.warning("Could not extract meaningful content")
            return None

        # Klassifiziere Topics
        topics = self._classify_topics(content + " " + summary)

        if not topics:
            logger.warning("No relevant topics detected")
            return None

        return {
            "raw_content": content,
            "context_summary": summary,
            "topics": topics,
            "timestamp": datetime.now().isoformat(),
            "chat_file": str(latest_chat),
            "should_sync": True,
        }

    def _find_latest_memory_file(self) -> Optional[Path]:
        """Finde neueste Memory-Datei."""
        if not self.memory_dir.exists():
            return None

        memory_files = list(self.memory_dir.glob("*.md"))
        if not memory_files:
            return None

        # Sort by modification time
        memory_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return memory_files[0]

    def _is_meta_talk(self, content: str) -> bool:
        """Prüfe ob Content nur Meta-Talk ist."""
        content_lower = content.lower()

        # Zähle Meta-Treffer
        meta_hits = sum(1 for pattern in self.META_PATTERNS if re.search(pattern, content_lower))
        
        # Wenn >70% Meta-Patterns, skippe
        if meta_hits > 2:
            return True

        # Wenn sehr kurz, wahrscheinlich Meta
        if len(content) < 100:
            return True

        return False

    def _summarize_content(self, content: str) -> str:
        """Erstelle Zusammenfassung aus Chat-Inhalt."""
        # Einfache Heuristik: Extrahiere wichtigste Zeilen
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        
        # Filtere Code-Blöcke, Timestamps, Meta-Stuff
        meaningful_lines = [
            l for l in lines
            if not l.startswith("```")
            and not l.startswith("[")
            and len(l) > 20
            and not any(pattern in l.lower() for pattern in ["token", "debug", "error"])
        ]

        if not meaningful_lines:
            return ""

        # Nehme erste 500 chars
        summary = " ".join(meaningful_lines)
        return summary[:500]

    def _classify_topics(self, content: str) -> List[str]:
        """Klassifiziere Topics basierend auf Keywords."""
        content_lower = content.lower()
        topics = []

        for topic, keywords in self.TOPIC_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw.lower() in content_lower)
            if hits >= 2:  # Mindestens 2 Keywords
                topics.append(topic)

        return list(set(topics))  # Unique


class ObsidianFileManager:
    """Verwende Obsidian Vault und schreibe neuen Kontext rein."""

    def __init__(self, vault_path: Path = VAULT_PATH):
        self.vault_path = vault_path
        self.modified_files = set()

    def append_to_relevant_files(self, context: Dict) -> Dict[str, str]:
        """
        Append Kontext zu relevanten Vault-Dateien.
        
        Returns:
        {
            'topic': 'file_path_where_appended',
            ...
        }
        """
        logger.info(f"📝 Appending context to {len(context['topics'])} topics...")

        target_files = self._map_topics_to_files(context["topics"])
        appended_files = {}

        for topic, file_path in target_files.items():
            full_path = self.vault_path / file_path

            # Ensure file exists
            full_path.parent.mkdir(parents=True, exist_ok=True)
            if not full_path.exists():
                full_path.write_text(f"# {full_path.stem}\n\n")

            # Appende Content
            entry = self._create_entry(context, topic)
            with open(full_path, "a", encoding="utf-8") as f:
                f.write(entry)

            appended_files[topic] = str(file_path)
            self.modified_files.add(str(file_path))
            logger.info(f"  ✓ Appended to {file_path}")

        return appended_files

    def _map_topics_to_files(self, topics: List[str]) -> Dict[str, str]:
        """Map Topics zu Vault-Dateien."""
        mapping = {
            "easysignals_products": "EasySignals/Products.md",
            "easysignals_learnings": "EasySignals/Learnings.md",
            "easysignals_processes": "EasySignals/Processes.md",
            "teletrade_api": "TeleTrade/API.md",
            "teletrade_crm": "TeleTrade/CRM.md",
            "teletrade_revenue": "TeleTrade/Revenue.md",
            "organization_decisions": "Organization/Decisions.md",
            "organization_okrs": "Organization/OKRs.md",
            "sops_operations": "SOPs/Operations.md",
            "sops_processes": "SOPs/Processes.md",
        }
        return {t: mapping[t] for t in topics if t in mapping}

    def _create_entry(self, context: Dict, topic: str) -> str:
        """Erstelle Entry für Vault-Datei."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        summary = context["context_summary"]
        
        entry = f"\n\n### {timestamp}\n\n{summary}\n\n_Source: Auto-sync from chat_"
        return entry


class SemanticBacklinkGenerator:
    """Regeneriere Backlinks nach neuer Kontext."""

    def __init__(self, documents: Dict):
        self.documents = documents
        self.backlinks = {}

    def generate(self) -> Dict:
        """Generiere semantische Backlinks."""
        logger.info("🧠 Regenerating semantic backlinks...")

        if not self.documents:
            return {}

        docs_list = list(self.documents.items())
        texts = [doc[1].get("content", "")[:500] for doc in docs_list]
        paths = [doc[0] for doc in docs_list]

        try:
            vectorizer = TfidfVectorizer(
                max_features=100,
                stop_words=["und", "oder", "ist", "die", "der", "das", "ein", "eine"],
                ngram_range=(1, 2),
            )
            tfidf_matrix = vectorizer.fit_transform(texts)
            similarity_matrix = cosine_similarity(tfidf_matrix)
        except Exception as e:
            logger.error(f"Error in TF-IDF: {e}")
            return {}

        for i, (path, doc) in enumerate(docs_list):
            similar_docs = []

            for j, other_path in enumerate(paths):
                if i == j:
                    continue

                weight = float(similarity_matrix[i][j])
                if weight >= MIN_SIMILARITY_WEIGHT:
                    similar_docs.append((other_path, weight))

            similar_docs.sort(key=lambda x: x[1], reverse=True)
            self.backlinks[path] = similar_docs[:MAX_BACKLINKS_PER_DOC]

        logger.info(f"✅ Generated backlinks for {len(self.backlinks)} documents")
        return self.backlinks


class VaultBacklinkUpdater:
    """Update Backlinks in Vault-Dateien."""

    def __init__(self, vault_path: Path = VAULT_PATH):
        self.vault_path = vault_path

    def parse_vault(self) -> Dict:
        """Parse alle Vault-Dateien."""
        documents = {}
        for md_file in self.vault_path.rglob("*.md"):
            if ".obsidian" in str(md_file):
                continue
            try:
                with open(md_file, "r", encoding="utf-8") as f:
                    content = f.read()
                rel_path = str(md_file.relative_to(self.vault_path))
                documents[rel_path] = {
                    "path": rel_path,
                    "full_path": str(md_file),
                    "content": content,
                    "title": Path(rel_path).stem,
                }
            except Exception as e:
                logger.warning(f"Could not parse {md_file}: {e}")
        return documents

    def update_backlinks(self, backlinks: Dict) -> Set[str]:
        """Update Backlinks in Dateien."""
        logger.info("🔗 Updating backlinks...")
        modified = set()

        for doc_path, related in backlinks.items():
            if not related:
                continue

            full_path = self.vault_path / doc_path
            if not full_path.exists():
                continue

            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Entferne alte Related Section
            content = re.sub(
                r"\n## Related.*?(?=\n##|\n---|\Z)",
                "",
                content,
                flags=re.DOTALL,
            ).rstrip()

            # Generiere neue Section
            backlinks_section = self._generate_backlinks_section(related, doc_path)
            content += "\n\n" + backlinks_section

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            modified.add(doc_path)

        logger.info(f"✅ Updated backlinks for {len(modified)} documents")
        return modified

    def _generate_backlinks_section(self, related: List[Tuple[str, float]], source_path: str) -> str:
        """Generiere Related Section."""
        if not related:
            return ""

        section = "## Related\n\n"
        for target_path, weight in related:
            title = Path(target_path).stem
            link_text = target_path.replace(".md", "")
            section += f"- [[{link_text}|{title}]] (relevance: {weight:.0%})\n"

        return section


class GitSyncManager:
    """Committe + Pushe Änderungen."""

    def __init__(self, vault_path: Path = VAULT_PATH):
        self.vault_path = vault_path

    def sync(self, modified_files: Set[str], context_summary: str = "") -> bool:
        """Git commit + push."""
        logger.info("🚀 Syncing to GitHub...")

        os.chdir(self.vault_path)

        # Stage
        os.system("git add -A")

        # Check changes
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
        )

        if not result.stdout.strip():
            logger.info("✅ No changes to commit")
            return True

        # Commit
        if context_summary:
            # Sanitize summary
            summary = context_summary[:50].replace("\n", " ")
            commit_msg = f"Auto-sync: {summary}"
        else:
            commit_msg = f"Auto-sync: Knowledge graph updated ({datetime.now().strftime('%Y-%m-%d %H:%M')})"

        result = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error(f"Commit failed: {result.stderr}")
            return False

        logger.info(f"✅ Committed: {commit_msg}")

        # Push
        result = subprocess.run(
            ["git", "push", "origin", "main"],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.warning(f"Push failed: {result.stderr}")
            return False

        logger.info("✅ Pushed to GitHub")
        return True


class ObsidianAutoSyncAgent:
    """Main Agent: Orchestriert Auto-Sync nach Chat."""

    def __init__(self):
        self.state = self._load_state()

    def _load_state(self) -> Dict:
        """Lade letzten Sync-State."""
        if SYNC_STATE.exists():
            try:
                with open(SYNC_STATE, "r") as f:
                    return json.load(f)
            except:
                pass
        return {"last_sync": None, "synced_files": []}

    def _save_state(self, last_chat_file: str, appended_files: Dict):
        """Speichere Sync-State."""
        self.state["last_sync"] = datetime.now().isoformat()
        self.state["last_chat_file"] = last_chat_file
        self.state["last_appended_files"] = appended_files
        
        with open(SYNC_STATE, "w") as f:
            json.dump(self.state, f, indent=2)

    def run(self) -> bool:
        """Führe Auto-Sync aus."""
        logger.info("=" * 70)
        logger.info("🚀 Starting Obsidian Auto-Sync Agent")
        logger.info("=" * 70)

        try:
            # 1. Extrahiere Chat-Kontext
            extractor = ChatContextExtractor()
            context = extractor.extract_from_latest_chat()

            if not context or not context.get("should_sync"):
                logger.info("No new context to sync")
                return True

            logger.info(f"✅ Extracted context with topics: {context['topics']}")

            # 2. Append zu Vault-Dateien
            file_manager = ObsidianFileManager()
            appended_files = file_manager.append_to_relevant_files(context)

            # 3. Parse Vault + regeneriere Backlinks
            updater = VaultBacklinkUpdater()
            documents = updater.parse_vault()
            
            backlink_gen = SemanticBacklinkGenerator(documents)
            backlinks = backlink_gen.generate()

            # 4. Update Backlinks
            modified_backlinks = updater.update_backlinks(backlinks)

            # 5. Git Sync
            all_modified = file_manager.modified_files | modified_backlinks
            git_manager = GitSyncManager()
            git_manager.sync(
                all_modified,
                context.get("context_summary", "Chat context auto-sync")
            )

            # 6. Speichere State
            self._save_state(context["chat_file"], appended_files)

            # Log Summary
            logger.info("=" * 70)
            logger.info("✅ Auto-Sync completed successfully!")
            logger.info(f"   Topics: {', '.join(context['topics'])}")
            logger.info(f"   Files updated: {len(all_modified)}")
            logger.info(f"   Appended to: {list(appended_files.values())}")
            logger.info("=" * 70)

            return True

        except Exception as e:
            logger.error(f"❌ Auto-Sync failed: {e}", exc_info=True)
            return False


if __name__ == "__main__":
    agent = ObsidianAutoSyncAgent()
    success = agent.run()
    sys.exit(0 if success else 1)
