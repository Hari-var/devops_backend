#!/usr/bin/env python3
"""
DevOps Backend Import Diagnostic and Fix Script
"""
import os
import sys
from pathlib import Path

def check_directory_structure():
    """Check if the expected directory structure exists."""
    
    print("📁 Checking Directory Structure...")
    print("-" * 40)
    
    base_dir = Path(__file__).parent
    expected_structure = {
        "backend": "Backend directory",
        "backend/app": "App directory", 
        "backend/app/services": "Services directory",
        "backend/app/api": "API directory",
        "backend/app/api/v1": "API v1 directory"
    }
    
    all_exist = True
    
    for path, description in expected_structure.items():
        full_path = base_dir / path
        if full_path.exists():
            print(f"   ✅ {description}: {full_path}")
        else:
            print(f"   ❌ {description}: MISSING - {full_path}")
            all_exist = False
    
    return all_exist

def check_service_files():
    """Check if all service files exist."""
    
    print("\n📄 Checking Service Files...")
    print("-" * 40)
    
    base_dir = Path(__file__).parent
    services_dir = base_dir / "backend" / "app" / "services"
    
    expected_files = [
        "subscriber_manager.py",
        "ai_config.py", 
        "ai_terraform_generator.py",
        "secure_pipeline_executor.py",
        "pipeline_flow_manager.py"
    ]
    
    all_exist = True
    
    for filename in expected_files:
        file_path = services_dir / filename
        if file_path.exists():
            print(f"   ✅ {filename}: EXISTS")
        else:
            print(f"   ❌ {filename}: MISSING")
            all_exist = False
    
    return all_exist

def check_init_files():
    """Check if __init__.py files exist where needed."""
    
    print("\n🐍 Checking __init__.py Files...")
    print("-" * 40)
    
    base_dir = Path(__file__).parent
    init_locations = [
        "backend/app/__init__.py",
        "backend/app/services/__init__.py",
        "backend/app/api/__init__.py",
        "backend/app/api/v1/__init__.py"
    ]
    
    missing_inits = []
    
    for init_path in init_locations:
        full_path = base_dir / init_path
        if full_path.exists():
            print(f"   ✅ {init_path}: EXISTS")
        else:
            print(f"   ❌ {init_path}: MISSING")
            missing_inits.append(full_path)
    
    return missing_inits

def create_missing_init_files(missing_inits):
    """Create missing __init__.py files."""
    
    if not missing_inits:
        return True
    
    print(f"\n🔧 Creating {len(missing_inits)} Missing __init__.py Files...")
    print("-" * 50)
    
    for init_path in missing_inits:
        try:
            # Create directory if it doesn't exist
            init_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create empty __init__.py file
            init_path.write_text("# Auto-generated __init__.py file\n")
            print(f"   ✅ Created: {init_path}")
        except Exception as e:
            print(f"   ❌ Failed to create {init_path}: {e}")
            return False
    
    return True

def test_imports_after_fix():
    """Test imports after applying fixes."""
    
    print("\n🧪 Testing Imports After Fix...")
    print("-" * 40)
    
    # Add backend to path
    backend_dir = Path(__file__).parent / "backend"
    sys.path.insert(0, str(backend_dir))
    
    test_imports = [
        ("app.services.subscriber_manager", "subscriber_manager"),
        ("app.services.ai_config", "AIConfig"),
        ("app.services.ai_terraform_generator", "AITerraformGenerator"),
        ("app.services.secure_pipeline_executor", "SecurePipelineExecutor"),
        ("app.services.pipeline_flow_manager", "PipelineFlowManager")
    ]
    
    success_count = 0
    
    for module_name, class_name in test_imports:
        try:
            module = __import__(module_name, fromlist=[class_name])
            getattr(module, class_name)
            print(f"   ✅ {module_name}.{class_name}: PASS")
            success_count += 1
        except Exception as e:
            print(f"   ❌ {module_name}.{class_name}: FAIL - {e}")
    
    return success_count == len(test_imports)

def main():
    """Main diagnostic and fix function."""
    
    print("🔧 DevOps Backend Import Diagnostic & Fix")
    print("=" * 60)
    
    # Step 1: Check directory structure
    structure_ok = check_directory_structure()
    
    # Step 2: Check service files
    files_ok = check_service_files()
    
    # Step 3: Check __init__.py files
    missing_inits = check_init_files()
    
    # Step 4: Create missing __init__.py files if needed
    if missing_inits:
        init_fix_ok = create_missing_init_files(missing_inits)
    else:
        init_fix_ok = True
        print("\n✅ All __init__.py files already exist")
    
    # Step 5: Test imports after fixes
    if structure_ok and files_ok and init_fix_ok:
        imports_ok = test_imports_after_fix()
    else:
        imports_ok = False
    
    # Final summary
    print("\n" + "=" * 60)
    print("📊 Diagnostic Results:")
    print(f"   Directory Structure: {'✅ OK' if structure_ok else '❌ ISSUES'}")
    print(f"   Service Files:       {'✅ OK' if files_ok else '❌ MISSING'}")
    print(f"   Init Files:          {'✅ OK' if init_fix_ok else '❌ ISSUES'}")
    print(f"   Import Tests:        {'✅ OK' if imports_ok else '❌ FAILED'}")
    
    if structure_ok and files_ok and init_fix_ok and imports_ok:
        print("\n🎉 ALL ISSUES RESOLVED!")
        print("✅ Your DevOps backend imports should now work correctly")
        return True
    else:
        print("\n⚠️ SOME ISSUES REMAIN")
        if not structure_ok:
            print("   • Check that all directories exist")
        if not files_ok:
            print("   • Ensure all service files are created")
        if not init_fix_ok:
            print("   • Fix __init__.py file creation issues")
        if not imports_ok:
            print("   • Check for syntax errors in service files")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)