#!/usr/bin/env bash
# يضبط ختم النشر لمتجر قوقل بلاي من سرَّي المستودع:
#   RELEASE_KEYSTORE_B64      ملف ختم النشر (upload key) بصيغة base64
#   KHDOOM_STORE_PASSWORD     كلمة مرور الختم (من السر RELEASE_KEYSTORE_PASSWORD)
# اسم المفتاح داخل الملف ثابت: upload. لا تُطبع أي كلمة مرور في السجل.
set -euo pipefail

if [ -z "${RELEASE_KEYSTORE_B64:-}" ] || [ -z "${KHDOOM_STORE_PASSWORD:-}" ]; then
  echo "خطأ: أضف السرين RELEASE_KEYSTORE_B64 و RELEASE_KEYSTORE_PASSWORD في إعدادات المستودع أولًا." >&2
  exit 1
fi

store="android/app/khdoom-upload.jks"
echo "$RELEASE_KEYSTORE_B64" | base64 -d > "$store"

echo "بصمة ختم النشر:"
if ! keytool -list -v -keystore "$store" -storepass "$KHDOOM_STORE_PASSWORD" -alias upload 2>/dev/null | grep "SHA256:"; then
  echo "خطأ: تعذر فتح ختم النشر. تأكد أن كلمة المرور صحيحة وأن اسم المفتاح upload." >&2
  exit 1
fi

cat >> android/app/build.gradle.kts <<'EOF'

android {
    signingConfigs {
        create("khdoomUpload") {
            storeFile = file("khdoom-upload.jks")
            storePassword = System.getenv("KHDOOM_STORE_PASSWORD")
            keyAlias = "upload"
            keyPassword = System.getenv("KHDOOM_STORE_PASSWORD")
        }
    }
    buildTypes {
        getByName("release") {
            signingConfig = signingConfigs.getByName("khdoomUpload")
            // بدون ضغط الكود في النسخ الأولى لتفادي أعطال لا تظهر إلا في النسخة النهائية.
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }
}
EOF
echo "تم ضبط ختم النشر."
