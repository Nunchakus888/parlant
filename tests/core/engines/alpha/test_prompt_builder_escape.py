# Copyright 2025 Emcie Co Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Test cases for escape_format_string function in prompt_builder.

These tests verify that user-provided content containing curly braces
(like {Product URL}, {Product Name}) doesn't cause str.format() errors.

Based on real production config from: app/case/online/prompt-format/config.json
"""

import pytest
from parlant.core.engines.alpha.prompt_builder import escape_format_string


class TestEscapeFormatString:
    """Test the escape_format_string utility function."""

    def test_product_url_placeholder(self):
        """Test escaping {Product URL} from communication_style config."""
        input_text = "Use this default sentence format when sending a product link: \n{Product URL}\nHello, This is {Product Name}."
        result = escape_format_string(input_text)
        
        assert "{{Product URL}}" in result
        assert "{{Product Name}}" in result
        # Count occurrences to ensure single braces are replaced with double
        assert result.count("{{Product URL}}") == 1
        assert result.count("{{Product Name}}") == 1

    def test_purchase_intent_message(self):
        """Test escaping purchase intent message from config.json line 24."""
        input_text = (
            'When the user says "I want one" or uses any similar expression indicating '
            'purchase intent (for example: "I need one", "I want to buy", "I want this"), '
            "reply with the following fixed message: {Product URL}  \n"
            "Open this page and click the 'Shop Now' button at the bottom."
        )
        result = escape_format_string(input_text)
        
        assert "{{Product URL}}" in result
        # Verify single braces are converted to double braces
        assert result.count("{{Product URL}}") == 1

    def test_no_braces(self):
        """Test that strings without braces are unchanged."""
        input_text = "Always greet the user warmly and provide clear, direct answers."
        result = escape_format_string(input_text)
        
        assert result == input_text

    def test_empty_string(self):
        """Test empty string handling."""
        assert escape_format_string("") == ""

    def test_non_string_input(self):
        """Test non-string input is converted to string."""
        assert escape_format_string(123) == "123"
        assert escape_format_string(None) == "None"

    def test_already_escaped_braces(self):
        """Test that already escaped braces get double-escaped (safe behavior)."""
        input_text = "Already escaped: {{test}}"
        result = escape_format_string(input_text)
        
        # {{test}} becomes {{{{test}}}} which is safe
        assert "{{{{test}}}}" in result

    def test_mixed_content_chinese(self):
        """Test mixed content with Chinese characters."""
        input_text = "产品链接: {Product URL} 请点击查看"
        result = escape_format_string(input_text)
        
        assert "{{Product URL}}" in result
        assert "产品链接:" in result
        assert "请点击查看" in result

    def test_attribute_access_attack(self):
        """Test that potential attribute access attacks are escaped."""
        malicious_inputs = [
            "{obj.__class__}",
            "{obj.__init__.__globals__}",
            "{user.__dict__}",
        ]
        
        for input_text in malicious_inputs:
            result = escape_format_string(input_text)
            # Should be escaped, preventing format() from interpreting it
            assert "{{" in result
            assert "}}" in result
            
            if input_text == "{obj.__class__}":
                assert "{{obj.__class__}}" in result
            elif input_text == "{obj.__init__.__globals__}":
                assert "{{obj.__init__.__globals__}}" in result
            elif input_text == "{user.__dict__}":
                assert "{{user.__dict__}}" in result

    def test_index_access_attack(self):
        """Test that potential index access attacks are escaped."""
        malicious_inputs = [
            "{obj[0]}",
            "{data[key]}",
            "{list[123]}",
        ]
        
        for input_text in malicious_inputs:
            result = escape_format_string(input_text)
            assert "{{" in result
            assert "}}" in result

            if input_text == "{obj[0]}":
                assert "{{obj[0]}}" in result
            elif input_text == "{data[key]}":
                assert "{{data[key]}}" in result
            elif input_text == "{list[123]}":
                assert "{{list[123]}}" in result


    def test_multiple_placeholders(self):
        """Test string with multiple placeholders."""
        input_text = "{name} - {price} - {url} - {description}"
        result = escape_format_string(input_text)
        
        assert result == "{{name}} - {{price}} - {{url}} - {{description}}"

    def test_nested_braces(self):
        """Test nested braces pattern."""
        input_text = "{{nested}} and {single}"
        result = escape_format_string(input_text)
        
        # {{nested}} becomes {{{{nested}}}}
        # {single} becomes {{single}}
        assert "{{{{nested}}}}" in result
        assert "{{single}}" in result


class TestCommunicationStyleIntegration:
    """Integration tests simulating the full prompt building flow."""

    def test_communication_style_in_template(self):
        """Test that communication_style with braces works in full template flow."""
        # Simulating config.json communication_style
        communication_style = [
            "Use clear, straightforward language.",
            "Use this default sentence format: \n{Product URL}\nHello, This is {Product Name}.",
            'When the user says "I want one", reply with: {Product URL}',
        ]
        
        # Escape items (as done in prompt_builder.py)
        escaped_items = [escape_format_string(str(item)) for item in communication_style]
        style_items = "\n".join(f"   - {item}" for item in escaped_items)
        
        # Verify escaped items contain double braces
        assert "{{Product URL}}" in style_items
        assert "{{Product Name}}" in style_items
        
        # Build template like actual code
        # Note: f-string will NOT consume {{ }} - those are preserved
        template = f"""
COMMUNICATION STYLE:
{style_items}
"""
        
        # Verify template still has double braces (f-string preserves them)
        assert "{{Product URL}}" in template
        
        # This should NOT raise KeyError
        try:
            result = template.format()  # Empty format call
            # After format(), {{...}} becomes {...}
            assert "{Product URL}" in result
            assert "{Product Name}" in result
        except KeyError as e:
            pytest.fail(f"KeyError should not be raised: {e}")

    def test_format_with_props(self):
        """Test template.format() with actual props doesn't conflict."""
        communication_style = [
            "{Product URL} - click here",
        ]
        
        escaped_items = [escape_format_string(str(item)) for item in communication_style]
        style_items = "\n".join(f"   - {item}" for item in escaped_items)
        
        # Template with actual placeholder
        template = f"""
Language: {{language}}
Style:
{style_items}
"""
        
        # Format with actual props
        result = template.format(language="English")
        
        assert "Language: English" in result
        assert "{Product URL}" in result  # Should render as single braces now
        assert "{{Product URL}}" not in result  # Double braces should be resolved

    def test_real_config_scenario(self):
        """Test with exact data from config.json."""
        # Exact communication_style from config.json
        communication_style = [
            "Use clear, straightforward language and avoid jargon or buzzwords. For example:\n- Say \"easy\" instead of \"frictionless\"\n- Say \"help\" instead of \"enable\"\n- Say \"start\" instead of \"onboard\"\n- Say \"use\" instead of \"leverage\"\n- Say \"choose\" instead of \"curate\"",
            "Always greet the user warmly and provide clear, direct answers.\nUse a friendly tone, but stay concise — avoid unnecessary small talk.\nProduct names must exactly match the information from our product database or knowledge base.\n",
            "Use this default sentence format when sending a product link: \n{Product URL}\nHello, This is {Product Name}. If you need it, you can place your order directly on the website. Cash on delivery.",
            "When the customer confirms an order, always reply with this message:Thank you, we will now arrange the delivery for you.",
            "Avoid emojis and slang. Use a tone that feels confident and trustworthy.",
            "Do not explain product specifications unless the customer specifically asks.",
            "Be short, clear, and professional.",
            "Do not repeat information about COD or delivery time in every message.",
            'When the user says "I want one" or uses any similar expression indicating purchase intent (for example: "I need one", "I want to buy", "I want this"), reply with the following fixed message: {Product URL}  \nOpen this page and click the \'Shop Now\' button at the bottom. Then fill in your delivery information. We will deliver the product to your doorstep in about 3-7 days.'
        ]
        
        # Process like prompt_builder.py does
        escaped_items = [escape_format_string(str(item)) for item in communication_style]
        style_items = "\n".join(f"   - {item}" for item in escaped_items)
        
        style_section = f"""
4. COMMUNICATION STYLE:
{style_items}
"""
        
        # Full template simulation
        template = f"""
LANGUAGE & COMMUNICATION REQUIREMENTS
-------------------------------------------------
1. PRIMARY RULE - LANGUAGE DETECTION:
Use the EXACT SAME language as the user's MOST RECENT message.

2. TONE RULE: 
Always maintain a professional tone in your responses.
{style_section}
5. CONSISTENCY: 
Each response should match the language.
"""
        
        # This should not raise any errors
        try:
            # Simulate str.format() call with props
            result = template.format()
            
            # Verify braces are properly escaped in output
            # After format(), {{...}} becomes {...}
            assert "{Product URL}" in result
            assert "{Product Name}" in result
            
            # Original unescaped patterns should not exist in template
            # (they would cause KeyError if they did)
            
        except KeyError as e:
            pytest.fail(f"Real config scenario failed with KeyError: {e}")
        except ValueError as e:
            pytest.fail(f"Real config scenario failed with ValueError: {e}")


class TestEdgeCases:
    """Edge case tests for robustness."""

    def test_only_opening_brace(self):
        """Test string with only opening brace."""
        result = escape_format_string("Price: {100")
        assert "{{100" in result

    def test_only_closing_brace(self):
        """Test string with only closing brace."""
        result = escape_format_string("Price: 100}")
        assert "100}}" in result

    def test_unmatched_braces(self):
        """Test string with unmatched braces."""
        result = escape_format_string("{a} {b {c}")
        assert "{{a}}" in result
        assert "{{b" in result
        assert "{{c}}" in result

    def test_newlines_and_special_chars(self):
        """Test preservation of newlines and special characters."""
        input_text = "{url}\nNew line\t\tTabs\r\nCRLF"
        result = escape_format_string(input_text)
        
        assert "{{url}}" in result
        assert "\n" in result
        assert "\t\t" in result
        assert "\r\n" in result

    def test_unicode_in_braces(self):
        """Test Unicode characters inside braces."""
        input_text = "{产品名称} and {Цена}"
        result = escape_format_string(input_text)
        
        assert "{{产品名称}}" in result
        assert "{{Цена}}" in result

    def test_json_like_content(self):
        """Test JSON-like content doesn't break."""
        input_text = '{"key": "value", "nested": {"inner": 1}}'
        result = escape_format_string(input_text)
        
        # JSON braces should be escaped
        assert "{{" in result
        assert "}}" in result
        
        # Should be safe for format()
        template = f"Data: {result}"
        formatted = template.format()  # Should not raise
        assert "key" in formatted

    def test_format_spec_like_content(self):
        """Test format spec-like content is escaped."""
        # These look like format specs but shouldn't be interpreted
        malicious = [
            "{0}",
            "{1:>10}",
            "{name!r}",
            "{value:.2f}",
        ]
        
        for input_text in malicious:
            result = escape_format_string(input_text)
            assert "{" not in result or "{{" in result

