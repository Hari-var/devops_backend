#!/usr/bin/env python3
"""
Test script for Google Gemini AI integration in DevOps backend.
"""
import os
import asyncio
import sys
from pathlib import Path

# Add the backend directory to Python path
backend_dir = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_dir))

from app.services.ai_terraform_generator import AITerraformGenerator, InfrastructureRequirements


async def test_gemini_integration():
    """Test Google Gemini AI integration."""
    
    # Check for API key
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY environment variable not set")
        print("Please set your Google Gemini API key:")
        print("export GEMINI_API_KEY='your-api-key-here'")
        return False
    
    print("🔑 Google Gemini API key found")
    
    try:
        # Initialize AI generator
        generator = AITerraformGenerator(api_key)
        print("✅ AITerraformGenerator initialized")
        
        # Create test requirements
        requirements = InfrastructureRequirements(
            app_type="web",
            language="python",
            framework="fastapi",
            expected_traffic="medium",
            database_required=True,
            cache_required=False,
            environment="dev",
            region="eastus",
            compliance_requirements=["encryption-at-rest"]
        )
        print("✅ Infrastructure requirements created")
        
        # Generate Terraform configuration
        print("🤖 Generating Terraform configuration with Google Gemini...")
        config_files = await generator.generate_terraform_config(
            requirements, 
            "test-app"
        )
        
        print(f"✅ Generated {len(config_files)} Terraform files:")
        for filename in config_files.keys():
            print(f"   - {filename}")
        
        # Validate main.tf exists and has content
        if 'main.tf' in config_files and len(config_files['main.tf']) > 100:
            print("✅ main.tf generated successfully")
            print(f"   Content length: {len(config_files['main.tf'])} characters")
        else:
            print("⚠️  main.tf seems incomplete")
        
        # Show a snippet of the generated configuration
        if 'main.tf' in config_files:
            print("\n📄 Sample of generated main.tf:")
            print("-" * 50)
            print(config_files['main.tf'][:300] + "...")
            print("-" * 50)
        
        print("\n🎉 Google Gemini integration test completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Error during Gemini integration test: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()
        return False


async def test_fallback_generation():
    """Test fallback configuration generation."""
    print("\n🔄 Testing fallback configuration generation...")
    
    try:
        # Initialize with dummy API key to trigger fallback
        generator = AITerraformGenerator("dummy-key")
        
        requirements = InfrastructureRequirements(
            app_type="api",
            language="python",
            framework="flask",
            expected_traffic="low",
            database_required=False,
            environment="dev"
        )
        
        # This should use fallback generation
        config_files = await generator.generate_terraform_config(
            requirements, 
            "fallback-test-app"
        )
        
        print(f"✅ Fallback generated {len(config_files)} files:")
        for filename in config_files.keys():
            print(f"   - {filename}")
        
        return True
        
    except Exception as e:
        print(f"❌ Fallback test failed: {e}")
        return False


async def main():
    """Main test function."""
    print("🚀 Starting Google Gemini AI Integration Tests")
    print("=" * 60)
    
    # Test 1: Gemini integration
    gemini_success = await test_gemini_integration()
    
    # Test 2: Fallback generation
    fallback_success = await test_fallback_generation()
    
    print("\n" + "=" * 60)
    print("📊 Test Results:")
    print(f"   Gemini Integration: {'✅ PASS' if gemini_success else '❌ FAIL'}")
    print(f"   Fallback Generation: {'✅ PASS' if fallback_success else '❌ FAIL'}")
    
    if gemini_success and fallback_success:
        print("\n🎉 All tests passed! Your Google Gemini integration is ready.")
    else:
        print("\n⚠️  Some tests failed. Please check the configuration.")
    
    return gemini_success and fallback_success


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)