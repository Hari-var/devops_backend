#!/usr/bin/env python3
"""
Simple import validation script for DevOps backend services.
"""
import sys
from pathlib import Path

def test_service_imports():
    """Test all service imports work correctly."""
    
    print(" Testing DevOps Backend Service Imports")
    print("=" * 50)
    
    # Add the backend directory to Python path
    backend_dir = Path(__file__).parent / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    
    success_count = 0
    total_tests = 5
    
    # Test 1: Subscriber Manager
    print("Testing subscriber_manager...")
    try:
        from app.services.subscriber_manager import subscriber_manager
        print("   subscriber_manager: PASS")
        success_count += 1
    except Exception as e:
        print(f"   subscriber_manager: FAIL - {e}")
    
    # Test 2: AI Config
    print("Testing ai_config...")
    try:
        from app.services.ai_config import AIConfig
        config_test = AIConfig.is_ai_enabled()
        print(f"   ai_config: PASS (AI enabled: {config_test})")
        success_count += 1
    except Exception as e:
        print(f"   ai_config: FAIL - {e}")
    
    # Test 3: AI Terraform Generator
    print("Testing ai_terraform_generator...")
    try:
        from app.services.ai_terraform_generator import AITerraformGenerator, InfrastructureRequirements
        # Test creating requirements object
        req = InfrastructureRequirements(
            app_type="web",
            language="python"
        )
        print(f"   ai_terraform_generator: PASS (req: {req.app_type})")
        success_count += 1
    except Exception as e:
        print(f"   ai_terraform_generator: FAIL - {e}")
    
    # Test 4: Secure Pipeline Executor
    print("Testing secure_pipeline_executor...")
    try:
        from app.services.secure_pipeline_executor import SecurePipelineExecutor
        print("   secure_pipeline_executor: PASS")
        success_count += 1
    except Exception as e:
        print(f"   secure_pipeline_executor: FAIL - {e}")
    
    # Test 5: Pipeline Flow Manager
    print("Testing pipeline_flow_manager...")
    try:
        from app.services.pipeline_flow_manager import PipelineFlowManager
        flow_manager = PipelineFlowManager()
        stages = flow_manager.stages
        print(f"   pipeline_flow_manager: PASS ({len(stages)} stages)")
        success_count += 1
    except Exception as e:
        print(f"   pipeline_flow_manager: FAIL - {e}")
    
    # Summary
    print("\n" + "=" * 50)
    print(f"Import Test Results: {success_count}/{total_tests}")
    print(f"Success Rate: {(success_count/total_tests)*100:.1f}%")
    
    if success_count == total_tests:
        print("All service imports working correctly!")
        print("Ready for DevOps pipeline execution")
        return True
    else:
        print("Some imports failed. Check error messages above.")
        return False

def test_yaml_dependency():
    """Test if PyYAML is available for CI/CD generation."""
    print("\nTesting YAML Dependency...")
    try:
        import yaml
        test_data = {"test": "data"}
        yaml_output = yaml.dump(test_data)
        print("   PyYAML: PASS")
        return True
    except ImportError:
        print("   PyYAML: FAIL - Run: pip install PyYAML")
        return False

def test_google_genai_dependency():
    """Test if Google Generative AI is available."""
    print("\n🤖 Testing Google Generative AI Dependency...")
    try:
        import google.generativeai as genai
        print("   google-generativeai: PASS")
        return True
    except ImportError:
        print("   google-generativeai: FAIL - Run: pip install google-generativeai")
        return False

def main():
    """Main test function."""
    print("🧪 DevOps Backend Import Validation")
    print("=" * 60)
    
    # Test service imports
    services_ok = test_service_imports()
    
    # Test dependencies
    yaml_ok = test_yaml_dependency()
    genai_ok = test_google_genai_dependency()
    
    # Final summary
    print("\n" + "=" * 60)
    print("🏁 Final Results:")
    print(f"   Service Imports: {'PASS' if services_ok else 'FAIL'}")
    print(f"   YAML Dependency: {'PASS' if yaml_ok else 'FAIL'}")
    print(f"   Google GenAI:    {'PASS' if genai_ok else 'FAIL'}")
    
    all_passed = services_ok and yaml_ok and genai_ok
    
    if all_passed:
        print("\nALL TESTS PASSED!")
        print("🚀 Your DevOps backend is ready to run!")
    else:
        print("\nSOME TESTS FAILED")
        if not yaml_ok or not genai_ok:
            print("💡 Install missing dependencies:")
            if not yaml_ok:
                print("   pip install PyYAML")
            if not genai_ok:
                print("   pip install google-generativeai")
    
    return all_passed

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)