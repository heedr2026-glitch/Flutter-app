import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:http/http.dart' as http;

import 'branch_store.dart';

/// الخدمات التي تتحكم إدارة خدووم بظهورها في التطبيق (الواتساب والمكالمات).
/// مخفية افتراضيًا، وتظهر فقط إذا شغّلتها الإدارة من لوحتها، بدون بناء نسخة جديدة.
class KhdoomAppFeatures {
  static final ValueNotifier<Map<String, bool>> current =
      ValueNotifier(const {'whatsapp': false, 'calls': false});

  static bool get whatsapp => current.value['whatsapp'] == true;
  static bool get calls => current.value['calls'] == true;

  static Future<void> refresh() async {
    try {
      final prefs = await BranchPreferences.getInstance();
      final base =
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com';
      final response = await http
          .get(Uri.parse(base).resolve('/api/app-features'))
          .timeout(const Duration(seconds: 15));
      if (response.statusCode != 200) return;
      final data = jsonDecode(response.body);
      if (data is! Map) return;
      final next = {
        'whatsapp': data['whatsapp'] == true,
        'calls': data['calls'] == true,
      };
      if (!mapEquals(next, current.value)) current.value = next;
    } catch (_) {
      // بدون اتصال تبقى الخدمتان مخفيتين كما هو الافتراضي.
    }
  }
}
