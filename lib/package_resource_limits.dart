import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import 'cloud_api.dart';

class PackageResourceLimits {
  static const defaults = PackageResourceLimits({
    'free': {
      'employees': 1,
      'users': 1,
      'vehicles': 1,
      'branches': 1,
      'organization_notifications': 5,
      'employee_notifications': 5,
    },
    'basic': {
      'employees': 5,
      'users': 5,
      'vehicles': 5,
      'branches': 3,
      'organization_notifications': 30,
      'employee_notifications': 30,
    },
    'vip': {
      'employees': null,
      'users': null,
      'vehicles': null,
      'branches': null,
      'organization_notifications': null,
      'employee_notifications': null,
    },
  });

  const PackageResourceLimits(this.values);
  final Map<String, Map<String, int?>> values;

  int? limit(String package, String resource) {
    final plan = values[package];
    if (plan == null) return package == 'vip' ? null : 0;
    return plan[resource] ??
        (resource == 'employees' ? plan['users'] : null) ??
        (resource == 'users' ? plan['employees'] : null) ??
        (package == 'vip' ? null : 0);
  }

  factory PackageResourceLimits.fromJson(Map<String, dynamic> json) {
    final result = <String, Map<String, int?>>{};
    for (final entry in json.entries) {
      final data = entry.value;
      if (data is! Map) throw const FormatException('Missing package limits');
      final plan = <String, int?>{};
      for (final resource in [
        'employees',
        'users',
        'vehicles',
        'branches',
        'organization_notifications',
        'employee_notifications',
      ]) {
        if (!data.containsKey(resource)) continue;
        final value = data[resource];
        if (value == null) {
          plan[resource] = null;
        } else if (value is int && value >= 0 && value <= 100000) {
          plan[resource] = value;
        } else {
          throw const FormatException('Invalid resource limit');
        }
      }
      if (!plan.containsKey('employees') && plan.containsKey('users')) {
        plan['employees'] = plan['users'];
      }
      if (!plan.containsKey('users') && plan.containsKey('employees')) {
        plan['users'] = plan['employees'];
      }
      result[entry.key] = plan;
    }
    return PackageResourceLimits(result);
  }

  static PackageResourceLimits cached(SharedPreferences prefs) {
    final baseUrl =
        prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com';
    final cacheKey = 'package_resource_limits_v2:$baseUrl';
    var cached = defaults;
    try {
      final raw = prefs.getString(cacheKey);
      if (raw != null) {
        cached = PackageResourceLimits.fromJson(
          Map<String, dynamic>.from(jsonDecode(raw) as Map),
        );
      }
    } catch (_) {
      // An invalid cache must never turn a limited plan into an unlimited one.
    }
    return cached;
  }

  static Future<PackageResourceLimits> load(SharedPreferences prefs) async {
    final baseUrl =
        prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com';
    final cacheKey = 'package_resource_limits_v2:$baseUrl';
    final fallback = cached(prefs);
    final api = KhdoomCloudApi(baseUrl: baseUrl);
    try {
      final json = await api.packageLimits().timeout(
        const Duration(seconds: 8),
      );
      final fresh = PackageResourceLimits.fromJson(json);
      await prefs.setString(cacheKey, jsonEncode(json));
      return fresh;
    } catch (_) {
      // Older servers and offline devices keep the last known limits.
      return fallback;
    } finally {
      api.close();
    }
  }
}
