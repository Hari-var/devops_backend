#!/usr/bin/env python3
"""
Import validation script to check all service imports work correctly.
"""
import sys
from pathlib import Path

# Add the backend directory to Python path
backend_dir = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_dir))

def test_imports():
    """Test all service imports."""
    
    print("🔍 Testing Service Imports")
    print("=" * 40)
    
    success_count = 0
    total_tests = 5
    
    # Test 1: Subscriber Manager
    try:
        from app.services.subscriber_manager import subscriber_manager
        print("✅ subscriber_manager import: PASS")
        success_count += 1
    except ImportError as e:
        print(f"❌ subscriber_manager import: FAIL - {e}")
    
    # Test 2: AI Config
    try:
        from app.services.ai_config import AIConfig
        print("✅ ai_config import: PASS")
        success_count += 1
    except ImportError as e:
        print(f"❌ ai_config import: FAIL - {e}")
    
    # Test 3: AI Terraform Generator
    try:
        from app.services.ai_terraform_generator import AITerraformGenerator, InfrastructureRequirements
        print("✅ ai_terraform_generator import: PASS")
        success_count += 1
    except ImportError as e:
        print(f"❌ ai_terraform_generator import: FAIL - {e}")
    
    # Test 4: Secure Pipeline Executor
    try:
        from app.services.secure_pipeline_executor import SecurePipelineExecutor
        print("✅ secure_pipeline_executor import: PASS")
        success_count += 1
    except ImportError as e:
        print(f"❌ secure_pipeline_executor import: FAIL - {e}")
    
    # Test 5: Pipeline Flow Manager
    try:
        from app.services.pipeline_flow_manager import PipelineFlowManager
        print("✅ pipeline_flow_manager import: PASS")
        success_count += 1
    except ImportError as e:
        print(f"❌ pipeline_flow_manager import: FAIL - {e}")
    
    # Summary
    print("\n" + "=" * 40)
    print(f"📊 Import Test Results: {success_count}/{total_tests}")
    
    if success_count == total_tests:
        print("🎉 All imports working correctly!")
        return True
    else:
        print("⚠️ Some imports failed. Check the error messages above.")
        return False

def test_relative_imports():
    """Test relative imports from approvals.py perspective."""
    
    print("\n🔗 Testing Relative Imports (from approvals.py)")
    print("=" * 50)
    
    try:
        # Add the app directory to path to simulate the relative import
        app_dir = Path(__file__).parent / "backend" / "app"
        sys.path.insert(0, str(app_dir))
        
        from services.subscriber_manager import subscriber_manager
        from services.ai_config import AIConfig
        from services.secure_pipeline_executor import SecurePipelineExecutor
        from services.pipeline_flow_manager import PipelineFlowManager
        
        print("✅ All relative imports from approvals.py context: PASS")
        return True
        
    except ImportError as e:
        print(f"❌ Relative imports: FAIL - {e}")
        return False

if __name__ == "__main__":
    print("🧪 Service Import Validation")
    print("=" * 60)
    
    # Test direct imports
    direct_success = test_imports()
    
    # Test relative imports
    relative_success = test_relative_imports()
    
    # Final result
    print("\n" + "=" * 60)
    if direct_success and relative_success:
        print("🎉 ALL IMPORT TESTS PASSED!")
        print("✅ Services are correctly importable")
        sys.exit(0)
    else:
        print("❌ SOME IMPORT TESTS FAILED")
        print("⚠️ Check the error messages and fix import paths")
        sys.exit(1)