#!/usr/bin/env python3
"""
Test AI Terraform Generation
Run this to test if Gemini AI is properly generating Terraform files and GitHub Actions workflow.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add the backend directory to Python path
sys.path.append(str(Path(__file__).parent / "backend"))

from backend.app.services.ai_terraform_generator import AITerraformGenerator, InfrastructureRequirements


async def test_ai_terraform_generation():
    """Test AI Terraform generation with sample requirements."""
    
    print("Testing AI Terraform + Workflow Generation")
    print("=" * 50)
    
    # Check if Gemini API key is available
    gemini_api_key = os.getenv("GOOGLE_GEMINI_API_KEY")
    if not gemini_api_key:
        print("GOOGLE_GEMINI_API_KEY environment variable not set")
        print("Set your API key: export GOOGLE_GEMINI_API_KEY=your_key_here")
        print("Get a key at: https://makersuite.google.com/app/apikey")
        return
    
    print(f"Gemini API key found: {gemini_api_key[:8]}...")
    
    # Create test requirements
    requirements = InfrastructureRequirements(
        app_type="web",
        language="python",
        framework="fastapi",
        expected_traffic="low",
        database_required=False,
        cache_required=False,
        environment="dev",
        region="eastus",
        compliance_requirements=[]
    )
    
    app_name = "test-app"
    
    print(f"\nGenerating Terraform + Workflow for: {app_name}")
    print(f"Requirements: {requirements.app_type} app, {requirements.language}/{requirements.framework}")
    
    try:
        # Initialize AI generator
        generator = AITerraformGenerator(gemini_api_key)
        
        # Generate Terraform configuration + workflow
        print("\nCalling Gemini AI...")
        terraform_files = await generator.generate_terraform_config(requirements, app_name)
        
        print("\nAI generation completed!")
        print(f"Generated {len(terraform_files)} files:")
        
        for filename, content in terraform_files.items():
            print(f"  • {filename}: {len(content)} characters")
            
            # Show first few lines of each file
            lines = content.split('\n')[:5]
            preview = '\n'.join(f"    {line}" for line in lines)
            print(f"    Preview:\n{preview}")
            if len(content.split('\n')) > 5:
                print("    ...")
            print()
        
        # Validate required files
        required_files = ['main.tf', 'variables.tf', 'outputs.tf', 'terraform-deploy.yml']
        missing_files = [f for f in required_files if f not in terraform_files]
        
        if missing_files:
            print(f"Missing required files: {', '.join(missing_files)}")
        else:
            print("All required files generated!")
        
        # Check for app_url output
        outputs_content = terraform_files.get('outputs.tf', '')
        if 'app_url' in outputs_content:
            print("app_url output found in outputs.tf")
        else:
            print("app_url output not found in outputs.tf")
        
        # Check for variables
        variables_content = terraform_files.get('variables.tf', '')
        if 'variable' in variables_content:
            print("Variables defined in variables.tf")
        else:
            print("No variables found in variables.tf")
        
        # Check for GitHub Actions workflow
        workflow_content = terraform_files.get('terraform-deploy.yml', '')
        if workflow_content:
            if 'name:' in workflow_content and 'jobs:' in workflow_content:
                print("Valid GitHub Actions workflow generated")
                if 'terraform' in workflow_content.lower():
                    print("Workflow contains Terraform steps")
                else:
                    print("Workflow may be missing Terraform steps")
            else:
                print("Generated workflow appears invalid")
        else:
            print("No GitHub Actions workflow generated")
        
        print("\n" + "=" * 50)
        print("AI Terraform + Workflow generation test completed!")
        
    except Exception as e:
        print(f"\nAI generation failed: {str(e)}")
        print("\nPossible solutions:")
        print("• Check your GOOGLE_GEMINI_API_KEY is valid")
        print("• Ensure you have internet connectivity")
        print("• Verify the API key has proper permissions")
        print("• Check if you've exceeded API quotas")


if __name__ == "__main__":
    asyncio.run(test_ai_terraform_generation())