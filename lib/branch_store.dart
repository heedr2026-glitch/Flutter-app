import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

/// Branch-owned records keep their legacy keys for the main office. No copying
/// or destructive migration is needed. Each handle captures an immutable scope.
class BranchPreferences implements SharedPreferences {
  final SharedPreferences raw;
  final String branchId;
  BranchPreferences(this.raw, this.branchId);
  static const mainId = 'main';
  static const registryKey = 'khdoom_branches_v1';
  static const activeKey = 'khdoom_active_branch_v1';

  static Map<String, dynamic> permissionsOf(dynamic value) {
    if (value is Map) return Map<String, dynamic>.from(value);
    if (value is String) {
      try {
        return Map<String, dynamic>.from(jsonDecode(value) as Map);
      } catch (_) {}
    }
    return {};
  }

  static String employeeBranch(Map employee) =>
      permissionsOf(employee['permissions'])['branch_id']?.toString() ?? mainId;

  Map<String, dynamic> tagEmployee(Map<String, dynamic> employee) => {
    ...employee,
    'permissions': {
      ...permissionsOf(employee['permissions']),
      'branch_id': branchId,
      'branch_name': branchName,
    },
  };

  /// Used only for account-level login/quotas; ordinary views stay branch-scoped.
  List<Map<String, dynamic>> allEmployees() => [
    for (final branch in branches(raw))
      for (final item in jsonDecode(
        BranchPreferences(raw, branch['id']!).getString('business_employees') ??
            '[]',
      ) as List)
        BranchPreferences(
          raw,
          branch['id']!,
        ).tagEmployee(Map<String, dynamic>.from(item as Map)),
  ];

  int employeeUsage(Iterable<dynamic> cloudEmployees) {
    final ids = cloudEmployees.map((e) => e['id'].toString()).toSet();
    for (final item in allEmployees()) {
      ids.add(item['id'].toString());
    }
    return ids.length;
  }

  /// Restore known branch names from the employee metadata already returned by
  /// the account API. Empty branches and local documents still need this device.
  Future<void> rememberEmployeeBranches(Iterable<dynamic> employees) async {
    final items = branches(raw);
    var changed = false;
    for (final employee in employees) {
      final permissions = permissionsOf(employee['permissions']);
      final id = permissions['branch_id']?.toString() ?? mainId;
      final name = permissions['branch_name']?.toString().trim() ?? '';
      if (id == mainId ||
          !RegExp(r'^[a-zA-Z0-9_-]{1,80}$').hasMatch(id) ||
          name.isEmpty ||
          name.length > 80)
        continue;
      if (!items.any((b) => b['id'] == id)) {
        items.add({'id': id, 'name': name});
        changed = true;
      }
    }
    if (changed && !await raw.setString(registryKey, jsonEncode(items))) {
      throw StateError('تعذر حفظ أسماء الفروع');
    }
  }

  static Future<BranchPreferences> getInstance() async {
    final raw = await SharedPreferences.getInstance();
    var id = raw.getString(activeKey) ?? mainId;
    if (raw.getString('session_user_type') == 'employee') {
      final permissions = permissionsOf(
        raw.getString('session_employee_permissions') ?? '{}',
      );
      id = permissions['branch_id']?.toString() ?? mainId;
    } else if (!branches(raw).any((b) => b['id'] == id)) {
      id = mainId;
    }
    return BranchPreferences(raw, id);
  }

  static List<Map<String, String>> branches(SharedPreferences raw) {
    final saved = raw.getString(registryKey);
    final items = saved == null ? <dynamic>[] : jsonDecode(saved) as List;
    return [
      {'id': mainId, 'name': 'المقر الرئيسي'},
      ...items
          .map((e) => Map<String, String>.from(e as Map))
          .where((e) => e['id'] != mainId),
    ];
  }

  String get branchName => branches(raw).firstWhere(
    (b) => b['id'] == branchId,
    orElse: () => {
      'name':
          permissionsOf(
            raw.getString('session_employee_permissions'),
          )['branch_name']?.toString() ??
          'الفرع',
    },
  )['name']!;

  Future<String> addBranch(String name, {String? registeredId}) async {
    _requireAdmin();
    final trimmed = name.trim();
    if (trimmed.isEmpty || trimmed.length > 80)
      throw ArgumentError('اكتب اسم الفرع من 1 إلى 80 حرفًا');
    final items = branches(raw);
    if (items.any(
      (b) => b['name']!.trim().toLowerCase() == trimmed.toLowerCase(),
    )) {
      throw ArgumentError('يوجد فرع بهذا الاسم');
    }
    final id = registeredId ?? 'b${DateTime.now().microsecondsSinceEpoch}';
    if (!RegExp(r'^[a-zA-Z0-9_-]{1,80}$').hasMatch(id)) throw ArgumentError('معرف الفرع غير صحيح');
    items.add({'id': id, 'name': trimmed});
    if (!await raw.setString(registryKey, jsonEncode(items)))
      throw StateError('تعذر حفظ الفرع');
    return id;
  }

  Future<void> selectBranch(String id) async {
    _requireAdmin();
    if (!branches(raw).any((b) => b['id'] == id))
      throw ArgumentError('الفرع غير موجود');
    if (!await raw.setString(activeKey, id))
      throw StateError('تعذر اختيار الفرع');
  }

  void _requireAdmin() {
    if (raw.getString('session_user_type') == 'employee')
      throw StateError('إدارة الفروع للمدير فقط');
  }

  static bool isScoped(String key) =>
      const {
        'businessName',
        'activity',
        'phone',
        'business_logo_path',
        'business_vehicles',
        'business_employees',
        'business_appointments_requests',
        'employee_alert_records',
        'daily_work_tasks_v1',
        'daily_work_hidden_v1',
        'organization_alert_records',
        'organization_alert_categories_v1',
        'electricity_bills',
        'seen_cloud_appointment_ids',
      }.contains(key) ||
      key.startsWith('alert_');

  String keyFor(String key) =>
      branchId == mainId || !isScoped(key) ? key : 'branch:$branchId:$key';

  /// Subscription quotas apply to the whole institution, not once per branch.
  int totalRecords(String key) {
    var count = 0;
    for (final branch in branches(raw)) {
      final saved = BranchPreferences(raw, branch['id']!).getString(key);
      if (saved != null) count += (jsonDecode(saved) as List).length;
    }
    return count;
  }

  @override
  Object? get(String key) => raw.get(keyFor(key));
  @override
  String? getString(String key) => raw.getString(keyFor(key));
  @override
  bool? getBool(String key) => raw.getBool(keyFor(key));
  @override
  int? getInt(String key) => raw.getInt(keyFor(key));
  @override
  double? getDouble(String key) => raw.getDouble(keyFor(key));
  @override
  List<String>? getStringList(String key) => raw.getStringList(keyFor(key));
  @override
  bool containsKey(String key) => raw.containsKey(keyFor(key));
  @override
  Set<String> getKeys() => raw
      .getKeys()
      .where(
        (key) =>
            !key.startsWith('branch:') &&
            (!isScoped(key) || branchId == mainId),
      )
      .followedBy(
        raw
            .getKeys()
            .where((key) => key.startsWith('branch:$branchId:'))
            .map((key) => key.substring('branch:$branchId:'.length)),
      )
      .toSet();
  @override
  Future<bool> setString(String key, String value) =>
      raw.setString(keyFor(key), value);
  @override
  Future<bool> setBool(String key, bool value) =>
      raw.setBool(keyFor(key), value);
  @override
  Future<bool> setInt(String key, int value) => raw.setInt(keyFor(key), value);
  @override
  Future<bool> setDouble(String key, double value) =>
      raw.setDouble(keyFor(key), value);
  @override
  Future<bool> setStringList(String key, List<String> value) =>
      raw.setStringList(keyFor(key), value);
  @override
  Future<bool> remove(String key) => raw.remove(keyFor(key));
  @override
  Future<void> reload() => raw.reload();
  @override
  Future<bool> clear() => throw UnsupportedError(
    'Use explicit branch record keys; never clear the institution',
  );
  @override
  Future<bool> commit() async => true;
}
