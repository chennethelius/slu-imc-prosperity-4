"""Extract code blocks and detect imports from Discord messages."""

import re

from config import CODE_BLOCK_RE
from storage.models import ExtractedCode

# Also match generic code blocks that look like Python
GENERIC_CODE_RE = re.compile(r"```\n(.*?)```", re.DOTALL)
PYTHON_INDICATORS = {"def ", "class ", "import ", "from ", "self.", "print(", "return "}
IMPORT_RE = re.compile(r"^(?:from\s+(\S+)\s+)?import\s+(.+)$", re.MULTILINE)


def extract_code_blocks(content: str) -> list[ExtractedCode]:
    """Extract Python code blocks from message content."""
    results = []

    # Explicit python blocks
    for code in CODE_BLOCK_RE.findall(content):
        code = code.strip()
        if code:
            results.append(ExtractedCode(
                language="python",
                code=code,
                imports=_extract_imports(code),
            ))

    # Generic code blocks that look like Python
    for code in GENERIC_CODE_RE.findall(content):
        code = code.strip()
        if code and _looks_like_python(code):
            # Skip if we already captured this as a python block
            if not any(cb.code == code for cb in results):
                results.append(ExtractedCode(
                    language="python",
                    code=code,
                    imports=_extract_imports(code),
                ))

    return results


def _looks_like_python(code: str) -> bool:
    """Heuristic: does this code block look like Python?"""
    return any(indicator in code for indicator in PYTHON_INDICATORS)


def _extract_imports(code: str) -> list[str]:
    """Extract import names from Python code."""
    imports = []
    for match in IMPORT_RE.finditer(code):
        from_module = match.group(1)
        import_names = match.group(2)
        if from_module:
            imports.append(from_module)
        else:
            for name in import_names.split(","):
                name = name.strip().split(" as ")[0].strip()
                if name:
                    imports.append(name)
    return imports
