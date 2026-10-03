#!/usr/bin/env bash
# يجهّز مشروع أندرويد من قالب Flutter ثم يضع ملفات خدووم فوقه؛ نفس خطوات بناء APK.
set -euo pipefail

rm -rf /tmp/khdoom_android
flutter create --platforms=android --org com.khdoom --project-name app /tmp/khdoom_android
rm -rf ./android
cp -R /tmp/khdoom_android/android ./android
cp build_support/AndroidManifest.xml android/app/src/main/AndroidManifest.xml
cp -R build_support/res/. android/app/src/main/res/
mkdir -p android/app/src/main/kotlin/com/khdoom/app
cp build_support/MainActivity.kt build_support/DriverTrackingService.kt build_support/DriverTrackingBridge.kt android/app/src/main/kotlin/com/khdoom/app/
sed -i 's/package com\.example\.untitled1/package com.khdoom.app/' android/app/src/main/kotlin/com/khdoom/app/MainActivity.kt
sed -i 's/compileSdk = flutter.compileSdkVersion/compileSdk = 37/' android/app/build.gradle.kts
sed -i '/compileOptions {/a\        isCoreLibraryDesugaringEnabled = true' android/app/build.gradle.kts
cat >> android/app/build.gradle.kts <<'EOF'

dependencies {
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.5")
}
EOF
echo "تم تجهيز مشروع أندرويد."
