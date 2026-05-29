"""
C.H.A.R.L.I.E. — Advanced Web Research & Code Analysis Toolkit
Provides comprehensive research, code analysis, and knowledge extraction capabilities.
"""

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import trafilatura
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

from charlie.security.tiers import RiskTier, risk_tier
from charlie.utils.logger import get_logger

logger = get_logger("ResearchToolkit")


class AdvancedResearchToolkit:
    """Comprehensive web research and code analysis toolkit."""

    def __init__(self, browser_req_q=None, browser_res_q=None):
        self.browser_req_q = browser_req_q
        self.browser_res_q = browser_res_q

    # ── Web Research Methods ───────────────────────────────────────────────

    @risk_tier(RiskTier.TIER_0)
    def deep_web_search(self, args: Dict[str, Any]) -> str:
        """Perform comprehensive web research with multiple sources."""
        query = args.get("query", "").strip()
        depth = args.get("depth", 3)  # How many layers deep to search

        if not query:
            return "No search query provided."

        try:
            results = []
            seen_urls = set()

            # Layer 1: DuckDuckGo search
            logger.info(f"Starting deep search for: {query}")
            with DDGS(timeout=15) as ddgs:
                search_results = list(ddgs.text(query, max_results=8))

            for result in search_results[:depth]:
                url = result.get('href', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)

                    # Fetch and analyze each page
                    page_data = self._analyze_webpage(url, result.get('body', ''))
                    if page_data:
                        results.append(page_data)

            # Layer 2: Follow internal links from top results
            if depth > 1 and results:
                top_result = results[0]
                internal_links = self._extract_internal_links(top_result.get('url', ''), top_result.get('content', ''))

                for link in internal_links[:3]:  # Limit to 3 internal links
                    if link not in seen_urls:
                        seen_urls.add(link)
                        page_data = self._analyze_webpage(link)
                        if page_data:
                            results.append(page_data)

            # Synthesize findings
            synthesis = self._synthesize_research_results(query, results)
            return synthesis

        except Exception as e:
            logger.error(f"Deep search failed: {e}")
            return f"Research failed: {e}"

    def _analyze_webpage(self, url: str, snippet: str = "") -> Optional[Dict[str, Any]]:
        """Analyze a single webpage for content and structure."""
        try:
            # Use trafilatura for content extraction with strict timeout
            html = trafilatura.fetch_url(url, timeout=10)
            if html:
                content = trafilatura.extract(html, include_comments=False, include_tables=True)
                if content and len(content) > 100:
                    soup = BeautifulSoup(html, 'html.parser')

                    # Extract metadata
                    title = soup.title.string if soup.title else "Untitled"
                    meta_desc = soup.find('meta', attrs={'name': 'description'})
                    description = meta_desc.get('content', '') if meta_desc else ""

                    # Extract headings and structure
                    headings = []
                    for h in soup.find_all(['h1', 'h2', 'h3']):
                        headings.append(f"{h.name}: {h.get_text().strip()}")

                    return {
                        'url': url,
                        'title': title,
                        'description': description,
                        'content': content[:2000],  # Truncate for synthesis
                        'headings': headings[:10],  # Top 10 headings
                        'word_count': len(content.split()),
                        'last_analyzed': time.time()
                    }
        except Exception as e:
            logger.debug(f"Failed to analyze {url}: {e}")

        return None

    def _extract_internal_links(self, base_url: str, html_content: str) -> List[str]:
        """Extract internal links from HTML content."""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            base_domain = urlparse(base_url).netloc

            internal_links = []
            for link in soup.find_all('a', href=True):
                href = link['href']
                full_url = urljoin(base_url, href)
                if urlparse(full_url).netloc == base_domain:
                    internal_links.append(full_url)

            return list(set(internal_links))[:5]  # Limit and deduplicate
        except Exception as e:
            logger.debug(f"Failed to extract internal links: {e}")
            return []

    def _synthesize_research_results(self, query: str, results: List[Dict[str, Any]]) -> str:
        """Synthesize research findings into coherent summary."""
        if not results:
            return f"No substantial information found for '{query}'."

        # Analyze patterns and key insights
        total_words = sum(r.get('word_count', 0) for r in results)
        [r.get('title', '') for r in results if r.get('title')]

        # Extract common themes and key points
        all_content = ' '.join(r.get('content', '') for r in results)
        key_insights = self._extract_key_insights(all_content, query)

        synthesis = f"Research Results for '{query}':\n\n"
        synthesis += f"📊 Analyzed {len(results)} sources ({total_words} words total)\n\n"

        synthesis += "🔑 Key Findings:\n"
        for insight in key_insights[:5]:  # Top 5 insights
            synthesis += f"• {insight}\n"

        synthesis += "\n📄 Top Sources:\n"
        for i, result in enumerate(results[:3], 1):
            title = result.get('title', 'Untitled')[:60]
            synthesis += f"{i}. {title}\n   {result.get('url', '')}\n"

        return synthesis

    def _extract_key_insights(self, content: str, query: str) -> List[str]:
        """Extract key insights from content using simple NLP."""
        sentences = re.split(r'[.!?]+', content)
        insights = []

        query_words = set(query.lower().split())

        for sentence in sentences:
            sentence_lower = sentence.lower()
            if len(sentence.split()) > 5:  # Substantial sentences only
                relevance_score = sum(1 for word in query_words if word in sentence_lower)
                if relevance_score > 0:
                    insights.append(sentence.strip())

        # Sort by relevance and length
        insights.sort(key=lambda x: (len(x), sum(1 for w in query_words if w in x.lower())), reverse=True)
        return insights[:10]

    # ── Code Analysis Methods ─────────────────────────────────────────────

    @risk_tier(RiskTier.TIER_0)
    def analyze_codebase(self, args: Dict[str, Any]) -> str:
        """Perform comprehensive code analysis on a directory."""
        path = args.get("path", ".")

        try:
            analysis = self._analyze_code_directory(path)
            return self._format_code_analysis(analysis)
        except Exception as e:
            return f"Code analysis failed: {e}"

    def _analyze_code_directory(self, root_path: str) -> Dict[str, Any]:
        """Analyze code directory structure and metrics."""
        root = Path(root_path)
        analysis = {
            'total_files': 0,
            'code_files': 0,
            'languages': {},
            'largest_files': [],
            'complexity_warnings': [],
            'structure': {}
        }

        # File extensions to language mapping
        lang_map = {
            '.py': 'Python',
            '.js': 'JavaScript',
            '.ts': 'TypeScript',
            '.java': 'Java',
            '.cpp': 'C++',
            '.c': 'C',
            '.go': 'Go',
            '.rs': 'Rust',
            '.php': 'PHP',
            '.rb': 'Ruby',
            '.swift': 'Swift',
            '.kt': 'Kotlin'
        }

        for file_path in root.rglob('*'):
            if file_path.is_file():
                analysis['total_files'] += 1
                ext = file_path.suffix.lower()

                if ext in lang_map:
                    analysis['code_files'] += 1
                    lang = lang_map[ext]
                    analysis['languages'][lang] = analysis['languages'].get(lang, 0) + 1

                    # Analyze individual file
                    file_info = self._analyze_code_file(file_path)
                    if file_info:
                        analysis['largest_files'].append(file_info)
                        if file_info.get('complexity_warning'):
                            analysis['complexity_warnings'].append(file_info)

        # Sort and limit results
        analysis['largest_files'].sort(key=lambda x: x['lines'], reverse=True)
        analysis['largest_files'] = analysis['largest_files'][:10]

        return analysis

    def _analyze_code_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Analyze individual code file."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            lines = content.split('\n')
            line_count = len(lines)

            # Basic complexity metrics
            functions = len(re.findall(r'\bdef\s+\w+\s*\(', content))  # Python functions
            classes = len(re.findall(r'\bclass\s+\w+', content))
            imports = len(re.findall(r'^\s*(import|from)\s', content, re.MULTILINE))

            complexity_score = functions + classes * 2  # Classes are more complex

            return {
                'path': str(file_path),
                'lines': line_count,
                'functions': functions,
                'classes': classes,
                'imports': imports,
                'complexity_score': complexity_score,
                'complexity_warning': complexity_score > 50
            }
        except Exception as e:
            logger.debug(f"Failed to analyze {file_path}: {e}")
            return None

    def _format_code_analysis(self, analysis: Dict[str, Any]) -> str:
        """Format code analysis results."""
        output = "📊 Codebase Analysis Results:\n\n"
        output += f"📁 Total Files: {analysis['total_files']}\n"
        output += f"💻 Code Files: {analysis['code_files']}\n\n"

        if analysis['languages']:
            output += "🌐 Languages:\n"
            for lang, count in sorted(analysis['languages'].items(), key=lambda x: x[1], reverse=True):
                output += f"  {lang}: {count} files\n"
            output += "\n"

        if analysis['largest_files']:
            output += "📏 Largest Files:\n"
            for file_info in analysis['largest_files'][:5]:
                output += f"  {file_info['path']}: {file_info['lines']} lines\n"
            output += "\n"

        if analysis['complexity_warnings']:
            output += "⚠️  Complexity Warnings:\n"
            for warning in analysis['complexity_warnings'][:5]:
                output += f"  {warning['path']}: {warning['complexity_score']} complexity score\n"

        return output

    @risk_tier(RiskTier.TIER_0)
    def search_code(self, args: Dict[str, Any]) -> str:
        """Search for code patterns across the codebase."""
        pattern = args.get("pattern", "")
        path = args.get("path", ".")

        if not pattern:
            return "No search pattern provided."

        try:
            results = self._grep_codebase(pattern, path)
            return self._format_search_results(results, pattern)
        except Exception as e:
            return f"Code search failed: {e}"

    def _grep_codebase(self, pattern: str, root_path: str) -> List[Dict[str, Any]]:
        """Search for pattern in codebase."""
        results = []
        root = Path(root_path)

        try:
            # Use ripgrep if available, fallback to Python implementation
            if self._has_ripgrep():
                results = self._ripgrep_search(pattern, root_path)
            else:
                results = self._python_grep(pattern, root)
        except Exception as e:
            logger.warning(f"Grep search failed, using Python fallback: {e}")
            results = self._python_grep(pattern, root)

        return results[:50]  # Limit results

    def _has_ripgrep(self) -> bool:
        """Check if ripgrep is available."""
        try:
            subprocess.run(['rg', '--version'], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _ripgrep_search(self, pattern: str, path: str) -> List[Dict[str, Any]]:
        """Use ripgrep for fast searching."""
        results = []
        try:
            cmd = ['rg', '--line-number', '--with-filename', pattern, path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            for line in result.stdout.split('\n'):
                if ':' in line:
                    parts = line.split(':', 2)
                    if len(parts) >= 3:
                        file_path, line_num, content = parts
                        results.append({
                            'file': file_path,
                            'line': int(line_num),
                            'content': content.strip()
                        })
        except subprocess.TimeoutExpired:
            logger.warning("Ripgrep search timed out")
        except Exception as e:
            logger.error(f"Ripgrep search failed: {e}")

        return results

    def _python_grep(self, pattern: str, root: Path) -> List[Dict[str, Any]]:
        """Fallback Python-based grep."""
        results = []
        try:
            compiled_pattern = re.compile(pattern, re.IGNORECASE)
            for file_path in root.rglob('*'):
                if file_path.is_file() and file_path.suffix in ['.py', '.js', '.ts', '.java', '.cpp', '.c', '.go', '.rs']:
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            for line_num, line in enumerate(f, 1):
                                if compiled_pattern.search(line):
                                    results.append({
                                        'file': str(file_path),
                                        'line': line_num,
                                        'content': line.strip()
                                    })
                                    if len(results) >= 50:  # Limit results
                                        break
                    except Exception:
                        continue
                if len(results) >= 50:
                    break
        except Exception as e:
            logger.error(f"Python grep failed: {e}")

        return results

    def _format_search_results(self, results: List[Dict[str, Any]], pattern: str) -> str:
        """Format search results."""
        if not results:
            return f"No matches found for pattern: {pattern}"

        output = f"🔍 Search Results for '{pattern}':\n\n"
        output += f"Found {len(results)} matches:\n\n"

        # Group by file
        file_groups = {}
        for result in results:
            file_path = result['file']
            if file_path not in file_groups:
                file_groups[file_path] = []
            file_groups[file_path].append(result)

        for file_path, matches in file_groups.items():
            output += f"📄 {file_path}:\n"
            for match in matches[:5]:  # Limit matches per file
                output += f"  Line {match['line']}: {match['content']}\n"
            if len(matches) > 5:
                output += f"  ... and {len(matches) - 5} more matches\n"
            output += "\n"

        return output

    @risk_tier(RiskTier.TIER_0)
    def analyze_dependencies(self, args: Dict[str, Any]) -> str:
        """Analyze project dependencies and security."""
        path = args.get("path", ".")

        try:
            deps = self._scan_dependencies(path)
            return self._format_dependency_analysis(deps)
        except Exception as e:
            return f"Dependency analysis failed: {e}"

    def _scan_dependencies(self, root_path: str) -> Dict[str, Any]:
        """Scan for dependency files and analyze them."""
        root = Path(root_path)
        deps = {
            'python': [],
            'javascript': [],
            'security_issues': [],
            'outdated': []
        }

        # Check Python dependencies
        if (root / 'requirements.txt').exists():
            deps['python'] = self._analyze_requirements(root / 'requirements.txt')

        if (root / 'pyproject.toml').exists():
            deps['python'].extend(self._analyze_pyproject(root / 'pyproject.toml'))

        # Check JavaScript dependencies
        if (root / 'package.json').exists():
            deps['javascript'] = self._analyze_package_json(root / 'package.json')

        return deps

    def _analyze_requirements(self, req_file: Path) -> List[Dict[str, Any]]:
        """Analyze requirements.txt file."""
        deps = []
        try:
            with open(req_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Parse package==version format
                        if '==' in line:
                            package, version = line.split('==', 1)
                            deps.append({'name': package, 'version': version, 'type': 'python'})
        except Exception as e:
            logger.debug(f"Failed to analyze requirements.txt: {e}")
        return deps

    def _analyze_pyproject(self, pyproject_file: Path) -> List[Dict[str, Any]]:
        """Analyze pyproject.toml dependencies."""
        deps = []
        try:
            import tomllib
            with open(pyproject_file, 'rb') as f:
                data = tomllib.load(f)

            # Extract dependencies from [project.dependencies] and [tool.poetry.dependencies]
            for section in ['project.dependencies', 'tool.poetry.dependencies']:
                if section in data:
                    deps_section = data[section]
                    for dep in deps_section:
                        deps.append({'name': dep, 'version': deps_section[dep], 'type': 'python'})
        except Exception as e:
            logger.debug(f"Failed to analyze pyproject.toml: {e}")
        return deps

    def _analyze_package_json(self, package_file: Path) -> List[Dict[str, Any]]:
        """Analyze package.json dependencies."""
        deps = []
        try:
            with open(package_file, 'r') as f:
                data = json.load(f)

            # Combine dependencies and devDependencies
            all_deps = {}
            all_deps.update(data.get('dependencies', {}))
            all_deps.update(data.get('devDependencies', {}))

            for dep, version in all_deps.items():
                deps.append({'name': dep, 'version': version, 'type': 'javascript'})
        except Exception as e:
            logger.debug(f"Failed to analyze package.json: {e}")
        return deps

    def _format_dependency_analysis(self, deps: Dict[str, Any]) -> str:
        """Format dependency analysis results."""
        output = "📦 Dependency Analysis:\n\n"

        for lang, packages in deps.items():
            if lang == 'security_issues':
                continue
            if packages:
                output += f"{lang.upper()} Dependencies ({len(packages)}):\n"
                for pkg in packages[:20]:  # Limit display
                    output += f"  {pkg['name']}: {pkg.get('version', 'latest')}\n"
                output += "\n"

        if deps.get('security_issues'):
            output += "🚨 Security Issues:\n"
            for issue in deps['security_issues'][:10]:
                output += f"  {issue}\n"

        return output
