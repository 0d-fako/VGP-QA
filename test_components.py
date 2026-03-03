"""
Test script to verify QA Test Agent functionality
"""
import os
import sys
from datetime import datetime

def test_imports():
    """Test that all modules can be imported"""
    print("Testing imports...")
    
    try:
        from config import config
        print("✓ Config module loaded")
        
        from models import Requirement, TestCase, TestExecution, PlaywrightConfig
        print("✓ Models module loaded")
        
        # Test model creation
        req = Requirement(
            id="TEST-001",
            title="Test Requirement",
            description="Test description",
            acceptance_criteria=["Criteria 1", "Criteria 2"],
            source_document="test.doc"
        )
        print("✓ Requirement model works")
        
        from models import TestStep
        tc = TestCase(
            id="TC-001",
            requirement_id="TEST-001",
            title="Test Case",
            steps=[TestStep(action="goto", value="{{url}}")],
            test_data={"key": "value"},
            expected_results=["Result 1"],
            playwright_script="# display only",
        )
        print("✓ TestCase model works")
        
        return True
        
    except Exception as e:
        print(f"✗ Import test failed: {e}")
        return False

def test_config():
    """Test configuration loading"""
    print("\nTesting configuration...")
    
    try:
        from config import config
        
        # Test default values
        assert config.PLAYWRIGHT_TIMEOUT == 30000
        assert config.AZURE_CONTAINER_NAME == "test-evidence"
        assert config.PLAYWRIGHT_HEADLESS == False
        print("✓ Configuration defaults work")
        
        return True
        
    except Exception as e:
        print(f"✗ Configuration test failed: {e}")
        return False

def test_playwright_config():
    """Test Playwright configuration"""
    print("\nTesting Playwright configuration...")
    
    try:
        from models import PlaywrightConfig
        
        # Test config creation
        pw_config = PlaywrightConfig(
            base_url="https://example.com",
            browser="chromium",
            headless=True
        )
        
        assert pw_config.base_url == "https://example.com"
        assert pw_config.browser == "chromium"
        assert pw_config.viewport == {"width": 1920, "height": 1080}
        print("✓ PlaywrightConfig works")
        
        return True
        
    except Exception as e:
        print(f"✗ Playwright config test failed: {e}")
        return False

def test_azure_storage():
    """Test Azure storage initialization"""
    print("\nTesting Azure storage...")
    
    try:
        from azure_storage import LocalStorageManager
        
        # Test local storage (since we don't have Azure configured)
        storage = LocalStorageManager()
        assert storage.is_configured() == True
        print("✓ Local storage manager works")
        
        return True
        
    except Exception as e:
        print(f"✗ Azure storage test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("QA Test Agent - Component Tests")
    print("=" * 40)
    
    tests = [
        test_imports,
        test_config,
        test_playwright_config,
        test_azure_storage
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        if test():
            passed += 1
    
    print("\n" + "=" * 40)
    print(f"Test Results: {passed}/{total} passed")
    
    if passed == total:
        print("🎉 All tests passed! The application is ready to run.")
        print("\nTo run the application:")
        print("1. Set your CLAUDE_API_KEY in .env file")
        print("2. Run: streamlit run app.py")
        return True
    else:
        print("❌ Some tests failed. Please check the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)