#!/usr/bin/env bash
# يختم التطبيق بختم ثابت محفوظ في سر المستودع DEBUG_KEYSTORE_B64 حتى تنزل التحديثات فوق بعضها.
# بدون السر يستمر البناء بختم مؤقت. إذا كان السر غير صالح يفشل البناء برسالة واضحة.
set -euo pipefail

if [ -z "${DEBUG_KEYSTORE_B64:-}" ]; then
  echo "تنبيه: لا يوجد ختم ثابت (DEBUG_KEYSTORE_B64)؛ سيُستخدم ختم مؤقت يتغير كل بناء."
  exit 0
fi

store="android/app/khdoom-debug.keystore"
echo "$DEBUG_KEYSTORE_B64" | base64 -d > "$store"

echo "بصمة الختم الثابت المستخدم في هذا البناء:"
if ! keytool -list -v -keystore "$store" -storepass android -alias androiddebugkey | grep "SHA256:"; then
  echo "خطأ: محتوى السر DEBUG_KEYSTORE_B64 ليس ملف ختم أندرويد صالحًا (debug.keystore)." >&2
  exit 1
fi

cat >> android/app/build.gradle.kts <<'EOF'

android {
    signingConfigs {
        getByName("debug") {
            storeFile = file("khdoom-debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }
}
EOF
echo "تم ضبط الختم الثابت في إعدادات البناء."
