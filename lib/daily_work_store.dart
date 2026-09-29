import 'dart:convert';

import 'branch_store.dart';

DateTime dayOnly(DateTime date) => DateTime(date.year, date.month, date.day);
DateTime? workDate(dynamic value) =>
    DateTime.tryParse(value?.toString() ?? '')?.toLocal();
String dateLabel(DateTime? date) => date == null
    ? 'غير محدد'
    : '${date.year}/${date.month.toString().padLeft(2, '0')}/${date.day.toString().padLeft(2, '0')}';
int daysFrom(DateTime date, DateTime today) => DateTime.utc(
  date.year,
  date.month,
  date.day,
).difference(DateTime.utc(today.year, today.month, today.day)).inDays;

class DailyWorkItem {
  final String id, title, detail, destination;
  final DateTime? due;
  final Map<String, dynamic>? task;
  final Map<String, dynamic>? source;
  const DailyWorkItem(
    this.id,
    this.title,
    this.detail,
    this.destination,
    this.due, {
    this.task,
    this.source,
  });
}

class DailyWorkStore {
  static const tasksKey = 'daily_work_tasks_v1';
  static const hiddenKey = 'daily_work_hidden_v1';
  static const employeesKey = 'employee_alert_records';
  final BranchPreferences prefs;
  DailyWorkStore(this.prefs);
  bool get isOwner => prefs.getString('session_user_type') == 'admin';
  void requireOwner() {
    if (!isOwner) throw StateError('هذه الصفحة لصاحب المؤسسة فقط');
  }

  List<Map<String, dynamic>> records(String key) {
    final decoded = jsonDecode(prefs.getString(key) ?? '[]');
    return (decoded as List)
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList();
  }

  Future<void> saveRecord(String key, Map<String, dynamic> item) async {
    requireOwner();
    if (key != tasksKey && key != employeesKey) {
      throw ArgumentError('سجل غير مدعوم');
    }
    final values = records(key);
    final index = values.indexWhere((e) => e['id'] == item['id']);
    if (index < 0) {
      values.add(item);
    } else {
      values[index] = {...values[index], ...item};
    }
    if (!await prefs.setString(key, jsonEncode(values))) {
      throw StateError('تعذر حفظ البيانات، حاول مرة أخرى');
    }
  }

  Future<void> setTaskDone(Map<String, dynamic> task, bool done) =>
      saveRecord(tasksKey, {...task, 'done': done});

  Future<void> deleteTask(String id) async {
    requireOwner();
    final values = records(tasksKey)
      ..removeWhere((e) => e['id'].toString() == id);
    if (!await prefs.setString(tasksKey, jsonEncode(values))) {
      throw StateError('تعذر حذف المهمة');
    }
  }

  String fingerprint(DailyWorkItem item) => jsonEncode([
    item.id,
    item.title,
    item.detail,
    item.due?.toIso8601String(),
  ]);
  bool isHidden(DailyWorkItem item) => records(hiddenKey)
      .any((e) => e['id'] == item.id && e['fingerprint'] == fingerprint(item));
  Future<void> setHidden(DailyWorkItem item, bool hidden) async {
    requireOwner();
    final values = records(hiddenKey)..removeWhere((e) => e['id'] == item.id);
    if (hidden) values.add({'id': item.id, 'fingerprint': fingerprint(item)});
    if (!await prefs.setString(hiddenKey, jsonEncode(values))) {
      throw StateError('تعذر تحديث اللوحة');
    }
  }

  List<DailyWorkItem> agenda(DateTime now, {bool includeHidden = false}) {
    requireOwner();
    final result = <DailyWorkItem>[];
    void expiry(
      String id,
      String title,
      dynamic value,
      String destination, [
      String detail = '',
      Map<String, dynamic>? source,
    ]) {
      final date = workDate(value);
      if (date == null || daysFrom(date, now) > 30) return;
      result.add(
        DailyWorkItem(id, title, detail, destination, date, source: source),
      );
    }

    for (final record in records('organization_alert_records')) {
      expiry(
        'org:${record['id']}',
        record['title']?.toString() ?? 'مستند المؤسسة',
        record['date'],
        'organization',
        'مستند: ${record['categoryTitle'] ?? record['title'] ?? ''}',
        record,
      );
    }
    if (!prefs.containsKey('organization_alert_records')) {
      for (final title in [
        'الرخصة البلدية',
        'السجل التجاري',
        'اشتراك قوى',
        'حماية الأجور – مدد',
        'شهادة الدفاع المدني',
      ]) {
        expiry(
          'org:$title',
          title,
          prefs.getString('alert_$title'),
          'organization',
          '',
          {'title': title},
        );
      }
    }
    for (final employee in records(employeesKey)) {
      if (employee['archived'] == true) continue;
      for (final field in {
        'iqama': 'الإقامة',
        'contract': 'العقد',
        'insurance': 'التأمين',
      }.entries) {
        expiry(
          'employee:${employee['id']}:${field.key}',
          '${field.value} — ${employee['name']}',
          employee[field.key],
          'employees',
          '',
          employee,
        );
      }
      for (final leave in (employee['leaves'] as List? ?? [])) {
        if (leave['status'] != 'approved') continue;
        final start = workDate(leave['start']);
        final end = workDate(leave['end']);
        if (start != null &&
            end != null &&
            daysFrom(start, now) <= 0 &&
            daysFrom(end, now) >= 0) {
          result.add(
            DailyWorkItem(
              'leave:${employee['id']}:${leave['id']}',
              '${employee['name']} في إجازة',
              'حتى ${dateLabel(end)}',
              'employees',
              dayOnly(now),
              source: employee,
            ),
          );
        }
      }
    }
    for (final vehicle in records('business_vehicles')) {
      for (final field in {
        'registration': 'استمارة',
        'inspection': 'فحص',
        'insurance': 'تأمين',
      }.entries) {
        expiry(
          'vehicle:${vehicle['id']}:${field.key}',
          '${field.value} ${vehicle['name']}',
          vehicle[field.key],
          'vehicles',
          vehicle['assignedEmployee']?.toString() ?? '',
          vehicle,
        );
      }
      if (['maintenance', 'broken', 'stopped'].contains(vehicle['status'])) {
        result.add(
          DailyWorkItem(
            'maintenance:${vehicle['id']}',
            'متابعة المركبة — ${vehicle['name']}',
            vehicle['assignedEmployee']?.toString() ?? 'بدون مسؤول',
            'vehicles',
            dayOnly(now),
            source: vehicle,
          ),
        );
      }
    }
    for (final bill in records('electricity_bills')) {
      if (bill['paid'] == true) continue;
      expiry(
        'bill:${bill['id']}',
        'فاتورة الكهرباء ${bill['month'] ?? ''}',
        bill['dueDate'],
        'bills',
        '',
        bill,
      );
    }
    for (final item in records('business_appointments_requests')) {
      if ([
        'completed',
        'rejected',
        'cancelled',
        'canceled',
      ].contains(item['status'])) {
        continue;
      }
      final date = workDate(item['date']);
      final followup =
          (num.tryParse('${item['followup_count'] ?? 0}') ?? 0) > 0;
      if (item['status'] == 'pending' ||
          followup ||
          (date != null && daysFrom(date, now) <= 0)) {
        result.add(
          DailyWorkItem(
            'appointment:${item['id']}',
            item['title']?.toString() ?? 'موعد عميل',
            '${item['customer'] ?? ''}${followup ? ' • طلب متابعة' : ''}${item['status'] == 'pending' ? ' • بانتظار القبول' : ''}',
            'appointments',
            date,
            source: item,
          ),
        );
      }
    }
    for (final task in records(tasksKey)) {
      if (task['done'] == true) continue;
      result.add(
        DailyWorkItem(
          'task:${task['id']}',
          task['title'].toString(),
          'المسؤول: ${task['assignee'] ?? 'صاحب المؤسسة'}',
          'task',
          workDate(task['due']),
          task: task,
        ),
      );
    }
    result.sort((a, b) {
      final comparison = (a.due ?? dayOnly(now)).compareTo(
        b.due ?? dayOnly(now),
      );
      return comparison == 0 ? a.id.compareTo(b.id) : comparison;
    });
    return includeHidden
        ? result
        : result.where((item) => !isHidden(item)).toList();
  }
}

String? validateLeave(
  DateTime? start,
  DateTime? end,
  List<dynamic> leaves,
  String status,
) {
  if (start == null || end == null) return 'حدد بداية الإجازة ونهايتها';
  if (dayOnly(end).isBefore(dayOnly(start))) {
    return 'نهاية الإجازة يجب أن تكون بعد بدايتها أو في نفس اليوم';
  }
  if (status == 'approved' &&
      leaves.any((leave) {
        final otherStart = workDate(leave['start']);
        final otherEnd = workDate(leave['end']);
        return leave['status'] == 'approved' &&
            otherStart != null &&
            otherEnd != null &&
            !dayOnly(end).isBefore(dayOnly(otherStart)) &&
            !dayOnly(start).isAfter(dayOnly(otherEnd));
      })) {
    return 'توجد إجازة معتمدة تتداخل مع هذه الفترة';
  }
  return null;
}
