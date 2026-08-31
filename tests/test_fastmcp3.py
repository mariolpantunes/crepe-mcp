"""Unit tests for FastMCP 3.X features: instructions, resources, prompts, and mounting."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure src/ is importable
SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from crepe_mcp.doc_store import new_document
from crepe_mcp.exporter import _render_office_to_pngs
from crepe_mcp.server import mcp as monolith_mcp
from crepe_mcp.server_diagrams import (
    drawio_diagram_prompt,
)
from crepe_mcp.server_diagrams import (
    mcp as diagrams_mcp,
)
from crepe_mcp.server_documents import (
    get_document_config_resource,
    get_document_markdown_resource,
    technical_report_prompt,
)
from crepe_mcp.server_documents import (
    mcp as documents_mcp,
)
from crepe_mcp.server_presentations import (
    academic_presentation_prompt,
    get_presentation_config_resource,
    get_presentation_markdown_resource,
)
from crepe_mcp.server_presentations import (
    mcp as presentations_mcp,
)
from crepe_mcp.server_research import (
    mcp as research_mcp,
)
from crepe_mcp.server_spreadsheets import (
    mcp as spreadsheets_mcp,
)
from crepe_mcp.server_spreadsheets import (
    spreadsheet_model_prompt,
)
from crepe_mcp.store import new_presentation, upsert_slide


class TestFastMCPInstructions(unittest.TestCase):
    """Test that all servers carry FastMCP 3.x instructions for agent guidance."""

    def test_monolith_instructions_present(self):
        self.assertTrue(bool(monolith_mcp.instructions))
        self.assertIn("Pandoc Markdown", monolith_mcp.instructions)
        self.assertIn("lint_", monolith_mcp.instructions)

    def test_subservers_instructions_present(self):
        servers = [
            presentations_mcp,
            documents_mcp,
            research_mcp,
            spreadsheets_mcp,
            diagrams_mcp,
        ]
        for s in servers:
            self.assertTrue(bool(s.instructions), f"Server {s.name} missing instructions")
            self.assertGreater(len(s.instructions), 20)


class TestFastMCPResources(unittest.TestCase):
    """Test FastMCP resource generators."""

    def test_presentation_resources(self):
        pres = new_presentation(title="Resource Presentation")
        upsert_slide(pres, index=0, title="Slide 1", content="Hello Resource")

        md = get_presentation_markdown_resource(pres.id)
        self.assertIn("Slide 1", md)
        self.assertIn("Hello Resource", md)

        cfg = get_presentation_config_resource(pres.id)
        self.assertIn("title: Resource Presentation", cfg)

    def test_document_resources(self):
        doc = new_document(title="Resource Document")
        md = get_document_markdown_resource(doc.id)
        self.assertIn("Resource Document", md)

        cfg = get_document_config_resource(doc.id)
        self.assertIn("Resource Document", cfg)


class TestFastMCPPrompts(unittest.TestCase):
    """Test FastMCP prompt template generators."""

    def test_academic_presentation_prompt(self):
        res = academic_presentation_prompt("Quantum Computing", "Physics Conference", slide_count=12)
        self.assertIn("Quantum Computing", res)
        self.assertIn("Physics Conference", res)
        self.assertIn("12-slide", res)
        self.assertIn("lint_presentation", res)

    def test_technical_report_prompt(self):
        res = technical_report_prompt("Architecture Specification", "Cloud Systems", chapter_count=5)
        self.assertIn("Architecture Specification", res)
        self.assertIn("Cloud Systems", res)
        self.assertIn("5 chapters", res)

    def test_drawio_diagram_prompt(self):
        res = drawio_diagram_prompt("Microservices Platform", "architecture")
        self.assertIn("Microservices Platform", res)
        self.assertIn("architecture", res)

    def test_spreadsheet_model_prompt(self):
        res = spreadsheet_model_prompt("Q3 Forecast", periods=6)
        self.assertIn("Q3 Forecast", res)
        self.assertIn("6 periods", res)


class TestLibreOfficeProfileIsolation(unittest.TestCase):
    """Test that LibreOffice headless execution uses profile isolation."""

    @patch("subprocess.run")
    @patch("os.path.isfile")
    @patch("os.remove")
    def test_user_installation_flag_passed(self, mock_remove, mock_isfile, mock_run):
        mock_isfile.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        with patch("crepe_mcp.exporter.render_pdf_to_pngs", return_value=["/tmp/slide_001.png"]):
            _render_office_to_pngs(["soffice"], "/tmp/sample.pptx", "/tmp/out", dpi=100)

        self.assertTrue(mock_run.called)
        cmd = mock_run.call_args[0][0]
        self.assertTrue(any(arg.startswith("-env:UserInstallation=file://") for arg in cmd))
        self.assertIn("--headless", cmd)
        self.assertIn("--convert-to", cmd)


class TestMonolithToolCount(unittest.TestCase):
    """Test that monolith exports all 40 sub-server tools."""

    def test_all_tools_mounted(self):
        tools = asyncio.run(monolith_mcp.list_tools())
        self.assertEqual(len(tools), 40)


if __name__ == "__main__":
    unittest.main()
