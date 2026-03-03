"""
Debug script to examine generated test cases and identify syntax errors
"""
import os
import sys
from dotenv import load_dotenv
load_dotenv()

from llm_processor import LLMProcessor
from models import PlaywrightConfig

def debug_test_generation():
    """Debug the test case generation process"""
    print("Debugging test case generation...")
    
    # Initialize LLM processor
    try:
        llm_processor = LLMProcessor()
        print("✓ LLM Processor initialized")
    except Exception as e:
        print(f"✗ Failed to initialize LLM Processor: {e}")
        return
    
    # Create sample requirements
    sample_requirements = """
    # Login Feature Requirements
    
    ## REQ-001: User Login
    **Description**: Users should be able to log into the application using email and password
    
    **Acceptance Criteria**:
    - User can enter email address in email field
    - User can enter password in password field  
    - User can click login button to submit credentials
    - System validates credentials and redirects to dashboard on success
    - System shows error message for invalid credentials
    
    ## REQ-002: Password Validation
    **Description**: Password field should have proper validation
    
    **Acceptance Criteria**:
    - Password field should mask entered characters
    - Minimum password length should be 8 characters
    - Password should be required field
    """
    
    try:
        # Analyze requirements
        print("\nAnalyzing requirements...")
        requirements = llm_processor.analyze_requirements(sample_requirements)
        print(f"✓ Extracted {len(requirements)} requirements")
        
        for req in requirements:
            print(f"  - {req.id}: {req.title}")
        
        # Generate test cases
        print("\nGenerating test cases...")
        test_cases = llm_processor.generate_test_cases(requirements)
        print(f"✓ Generated {len(test_cases)} test cases")
        
        # Examine generated test cases
        print("\nExamining generated test cases...")
        for i, tc in enumerate(test_cases):
            print(f"\n--- Test Case {i+1}: {tc.title} ---")
            print(f"ID: {tc.id}")
            print(f"Requirement ID: {tc.requirement_id}")
            print(f"Test Data: {tc.test_data}")
            print(f"Expected Results: {tc.expected_results}")
            print("Playwright Script:")
            print("=" * 50)
            print(tc.playwright_script)
            print("=" * 50)
            
            # Try to validate the script syntax
            try:
                # Test if the script can be compiled
                compile(tc.playwright_script, '<string>', 'exec')
                print("✓ Script syntax is valid")
            except SyntaxError as e:
                print(f"✗ Script syntax error: {e}")
                print(f"  Line {e.lineno}: {e.text}")
            except Exception as e:
                print(f"✗ Script validation error: {e}")
                
    except Exception as e:
        print(f"✗ Error in test generation: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    debug_test_generation()