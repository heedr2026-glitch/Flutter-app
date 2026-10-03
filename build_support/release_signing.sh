#!/usr/bin/env bash
# يضبط ختم النشر لمتجر قوقل بلاي من سرَّي المستودع:
#   RELEASE_KEYSTORE_B64      ملف ختم النشر (upload key) بصيغة base64
#   KHDOOM_STORE_PASSWORD     كلمة مرور الختم (من السر RELEASE_KEYSTORE_PASSWORD)
# اسم المفتاح (alias) يُقرأ تلقائيًا من ملف الختم نفسه. لا تُطبع أي كلمة مرور في السجل.
set -euo pipefail

if [ -z "${RELEASE_KEYSTORE_B64:-}" ] || [ -z "${KHDOOM_STORE_PASSWORD:-}" ]; then
  echo "خطأ: أضف السرين RELEASE_KEYSTORE_B64 و RELEASE_KEYSTORE_PASSWORD في إعدادات المستودع أولًا." >&2
  exit 1
fi

store="android/app/khdoom-upload.jks"
echo "$RELEASE_KEYSTORE_B64" | base64 -d > "$store"

listing="$(keytool -list -keystore "$store" -storepass "$KHDOOM_STORE_PASSWORD" 2>/dev/null || true)"
alias_name="$(printf '%s\n' "$listing" | grep "PrivateKeyEntry" | head -n 1 | cut -d, -f1)"
if [ -z "$alias_name" ]; then
  echo "خطأ: تعذر فتح ختم النشر. تأكد أن كلمة المرور في السر RELEASE_KEYSTORE_PASSWORD صحيحة لهذا الملف." >&2
  exit 1
fi
if ! printf '%s' "$alias_name" | grep -Eq '^[A-Za-z0-9._-]+$'; then
  echo "خطأ: اسم المفتاح داخل ملف الختم يحتوي رموزًا غير مدعومة." >&2
  exit 1
fi
echo "اسم المفتاح: $alias_name"
echo "بصمة ختم النشر:"
keytool -list -v -keystore "$store" -storepass "$KHDOOM_STORE_PASSWORD" -alias "$alias_name" 2>/dev/null | grep -E "SHA1:|SHA256:"

cat >> android/app/build.gradle.kts <<'EOF'

android {
    signingConfigs {
        create("khdoomUpload") {
            storeFile = file("khdoom-upload.jks")
            storePassword = System.getenv("KHDOOM_STORE_PASSWORD")
            keyAlias = "__KHDOOM_KEY_ALIAS__"
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
sed -i "s/__KHDOOM_KEY_ALIAS__/$alias_name/" android/app/build.gradle.kts
echo "تم ضبط ختم النشر."
