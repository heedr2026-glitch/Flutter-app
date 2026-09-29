import 'package:flutter/material.dart';

import 'branch_store.dart';
import 'daily_work_store.dart';
import 'khdoom_dark_page.dart';

class EmployeeManagementPage extends StatefulWidget {
  final Future<void> Function() onAlertsChanged;
  const EmployeeManagementPage({super.key, required this.onAlertsChanged});
  @override
  State<EmployeeManagementPage> createState() => _EmployeeManagementPageState();
}

class _EmployeeManagementPageState extends State<EmployeeManagementPage> {
  DailyWorkStore? _store;
  List<Map<String, dynamic>> _employees = [];
  String? _error;
  String _query = '';
  bool _archived = false;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final store =
          _store ?? DailyWorkStore(await BranchPreferences.getInstance());
      store.requireOwner();
      final records = store.records(DailyWorkStore.employeesKey);
      if (mounted) {
        setState(() {
          _store = store;
          _employees = records;
          _error = null;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(
          () => _error = 'تعذر فتح البيانات أو لا تملك صلاحية صاحب المؤسسة',
        );
      }
    }
  }

  Future<void> _edit([Map<String, dynamic>? employee]) async {
    await Navigator.push<void>(
      context,
      MaterialPageRoute(
        builder: (_) => EmployeeProfileEditor(
          store: _store!,
          initial: employee,
          onAlertsChanged: widget.onAlertsChanged,
        ),
      ),
    );
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final visible = _employees
        .where(
          (e) =>
              (e['archived'] == true) == _archived &&
              '${e['name']} ${e['jobTitle'] ?? ''} ${e['phone'] ?? ''}'
                  .contains(_query),
        )
        .toList();
    return KhdoomDarkPage(
      child: Directionality(
        textDirection: TextDirection.rtl,
        child: Scaffold(
          appBar: AppBar(
            title: const Text('إدارة الموظفين'),
            actions: [
              IconButton(
                onPressed: _load,
                tooltip: 'تحديث',
                icon: const Icon(Icons.refresh),
              ),
            ],
          ),
          floatingActionButton: _store == null
              ? null
              : FloatingActionButton.extended(
                  onPressed: () => _edit(),
                  icon: const Icon(Icons.person_add_alt_1),
                  label: const Text('إضافة ملف موظف'),
                ),
          body: _error != null
              ? Center(child: Text(_error!))
              : _store == null
              ? const Center(child: CircularProgressIndicator())
              : ListView(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 100),
                  children: [
                    Text(
                      _store!.prefs.branchName,
                      style: const TextStyle(
                        fontSize: 22,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'الملفات والرواتب والإجازات محفوظة على هذا الجهاز. إنشاء الملف لا ينشئ حساب دخول.',
                      style: TextStyle(color: Colors.white70),
                    ),
                    const SizedBox(height: 16),
                    TextField(
                      decoration: const InputDecoration(
                        labelText: 'بحث بالاسم أو الوظيفة أو الجوال',
                        prefixIcon: Icon(Icons.search),
                      ),
                      onChanged: (v) => setState(() => _query = v.trim()),
                    ),
                    const SizedBox(height: 12),
                    Wrap(
                      spacing: 8,
                      children: [
                        ChoiceChip(
                          label: Text(
                            'الملفات الحالية (${_employees.where((e) => e['archived'] != true).length})',
                          ),
                          selected: !_archived,
                          onSelected: (_) => setState(() => _archived = false),
                        ),
                        ChoiceChip(
                          label: const Text('المؤرشفة'),
                          selected: _archived,
                          onSelected: (_) => setState(() => _archived = true),
                        ),
                      ],
                    ),
                    if (visible.isEmpty)
                      const Padding(
                        padding: EdgeInsets.all(32),
                        child: Text(
                          'لا توجد ملفات مطابقة. أضف ملفًا لتتابع الوثائق والإجازات.',
                          textAlign: TextAlign.center,
                        ),
                      ),
                    for (final employee in visible)
                      Card(
                        child: ListTile(
                          leading: const CircleAvatar(
                            child: Icon(Icons.person_outline),
                          ),
                          title: Text(employee['name']?.toString() ?? 'موظف'),
                          subtitle: Text(
                            '${employee['jobTitle'] ?? 'الوظيفة غير محددة'}\n${_expirySummary(employee)}',
                          ),
                          isThreeLine: true,
                          trailing: const Icon(Icons.chevron_left),
                          onTap: () => _edit(employee),
                        ),
                      ),
                  ],
                ),
        ),
      ),
    );
  }

  String _expirySummary(Map<String, dynamic> employee) {
    final dates = [
      'iqama',
      'contract',
      'insurance',
    ].map((k) => workDate(employee[k])).whereType<DateTime>().toList()..sort();
    if (dates.isEmpty) return 'لم تُضف تواريخ الوثائق';
    final days = daysFrom(dates.first, DateTime.now());
    return days < 0
        ? 'وثيقة منتهية منذ ${-days} يوم'
        : days == 0
        ? 'وثيقة تنتهي اليوم'
        : 'أقرب انتهاء خلال $days يوم';
  }
}

class EmployeeProfileEditor extends StatefulWidget {
  final DailyWorkStore store;
  final Map<String, dynamic>? initial;
  final Future<void> Function() onAlertsChanged;
  const EmployeeProfileEditor({
    super.key,
    required this.store,
    this.initial,
    required this.onAlertsChanged,
  });
  @override
  State<EmployeeProfileEditor> createState() => _EmployeeProfileEditorState();
}

class _EmployeeProfileEditorState extends State<EmployeeProfileEditor> {
  final _form = GlobalKey<FormState>();
  final _controllers = <String, TextEditingController>{};
  final _dates = <String, DateTime?>{};
  List<Map<String, dynamic>> _leaves = [];
  bool _saving = false, _archived = false;
  String? _error;
  String? _accountId;
  static const fields = {
    'name': 'اسم الموظف',
    'jobTitle': 'المسمى الوظيفي',
    'phone': 'رقم الجوال',
    'nationality': 'الجنسية',
    'identityNumber': 'رقم الهوية / الإقامة',
    'salary': 'الراتب الشهري (ر.س)',
    'notes': 'ملاحظات إدارية',
  };
  static const dateFields = {
    'joinedAt': 'تاريخ الالتحاق',
    'iqama': 'انتهاء الهوية / الإقامة',
    'contract': 'انتهاء العقد',
    'insurance': 'انتهاء التأمين',
  };
  @override
  void initState() {
    super.initState();
    for (final key in fields.keys) {
      _controllers[key] = TextEditingController(
        text: widget.initial?[key]?.toString() ?? '',
      );
    }
    for (final key in dateFields.keys) {
      _dates[key] = workDate(widget.initial?[key]);
    }
    _leaves = (widget.initial?['leaves'] as List? ?? [])
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList();
    _archived = widget.initial?['archived'] == true;
    _accountId = widget.initial?['accountId']?.toString();
  }

  @override
  void dispose() {
    for (final c in _controllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _pickDate(String key) async {
    final date = await showDatePicker(
      context: context,
      initialDate: _dates[key] ?? DateTime.now(),
      firstDate: DateTime(1900),
      lastDate: DateTime(2200),
    );
    if (date != null && mounted) setState(() => _dates[key] = date);
  }

  Future<void> _save() async {
    if (_saving || !_form.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await widget.store.saveRecord(DailyWorkStore.employeesKey, {
        ...?widget.initial,
        'id':
            widget.initial?['id'] ??
            'hr_${DateTime.now().microsecondsSinceEpoch}',
        for (final key in fields.keys) key: _controllers[key]!.text.trim(),
        for (final key in dateFields.keys)
          key: _dates[key]?.toIso8601String() ?? '',
        'accountId': _accountId,
        'archived': _archived,
        'leaves': _leaves,
        'updatedAt': DateTime.now().toIso8601String(),
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error =
              'تعذر حفظ الملف. بيانات النموذج ما زالت موجودة، حاول مرة أخرى.';
        });
      }
      return;
    }
    var notificationFailed = false;
    try {
      await widget.onAlertsChanged();
    } catch (_) {
      notificationFailed = true;
    }
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(
          notificationFailed
              ? 'حُفظ الملف؛ تعذر تحديث إشعارات الجهاز، أعد المحاولة من التنبيهات.'
              : 'تم حفظ ملف الموظف',
        ),
      ),
    );
    Navigator.pop(context);
  }

  Future<void> _addLeave() async {
    final leave = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => _LeaveDialog(existing: _leaves),
    );
    if (leave != null && mounted) setState(() => _leaves.add(leave));
  }

  @override
  Widget build(BuildContext context) {
    if (!widget.store.isOwner) {
      return const Scaffold(
        body: Center(child: Text('هذه الصفحة لصاحب المؤسسة فقط')),
      );
    }
    final accounts = widget.store.records('business_employees');
    return KhdoomDarkPage(
      child: Directionality(
        textDirection: TextDirection.rtl,
        child: Scaffold(
          appBar: AppBar(
            title: Text(
              widget.initial == null ? 'ملف موظف جديد' : 'ملف الموظف',
            ),
          ),
          body: Form(
            key: _form,
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                if (accounts.isNotEmpty)
                  DropdownButtonFormField<String>(
                    initialValue:
                        accounts.any((e) => e['id'].toString() == _accountId)
                        ? _accountId
                        : null,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'ربط بحساب موظف موجود (اختياري)',
                    ),
                    items: accounts
                        .map(
                          (e) => DropdownMenuItem(
                            value: e['id'].toString(),
                            child: Text(
                              e['name']?.toString() ?? 'موظف',
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        )
                        .toList(),
                    onChanged: _saving
                        ? null
                        : (id) {
                            final account = accounts.firstWhere(
                              (e) => e['id'].toString() == id,
                            );
                            setState(() {
                              _accountId = id;
                              if (_controllers['name']!.text.isEmpty) {
                                _controllers['name']!.text =
                                    account['name']?.toString() ?? '';
                              }
                              if (_controllers['phone']!.text.isEmpty) {
                                _controllers['phone']!.text =
                                    account['phone']?.toString() ?? '';
                              }
                              if (_controllers['jobTitle']!.text.isEmpty) {
                                _controllers['jobTitle']!.text =
                                    account['role']?.toString() ?? '';
                              }
                            });
                          },
                  ),
                for (final field in fields.entries)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: TextFormField(
                      controller: _controllers[field.key],
                      enabled: !_saving,
                      decoration: InputDecoration(labelText: field.value),
                      keyboardType: field.key == 'salary'
                          ? const TextInputType.numberWithOptions(decimal: true)
                          : field.key == 'phone'
                          ? TextInputType.phone
                          : TextInputType.text,
                      maxLines: field.key == 'notes' ? 3 : 1,
                      validator: (value) {
                        if (field.key == 'name' &&
                            (value ?? '').trim().isEmpty) {
                          return 'اكتب اسم الموظف';
                        }
                        if (field.key == 'salary' &&
                            (value ?? '').trim().isNotEmpty) {
                          final number = double.tryParse(value!.trim());
                          if (number == null ||
                              !number.isFinite ||
                              number < 0) {
                            return 'اكتب راتبًا صحيحًا لا يقل عن صفر';
                          }
                        }
                        return null;
                      },
                    ),
                  ),
                const SizedBox(height: 16),
                const Text(
                  'الوثائق والتنبيهات',
                  style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                ),
                const Text(
                  'تظهر الوثائق القريبة من الانتهاء في اللوحة اليومية، وتستخدم إعدادات تنبيهات الجهاز.',
                ),
                for (final field in dateFields.entries)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.calendar_month_outlined),
                    title: Text(field.value),
                    subtitle: Text(dateLabel(_dates[field.key])),
                    onTap: _saving ? null : () => _pickDate(field.key),
                    trailing: _dates[field.key] == null
                        ? null
                        : IconButton(
                            tooltip: 'مسح التاريخ',
                            onPressed: _saving
                                ? null
                                : () =>
                                      setState(() => _dates[field.key] = null),
                            icon: const Icon(Icons.clear),
                          ),
                  ),
                const Divider(),
                Wrap(
                  alignment: WrapAlignment.spaceBetween,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    const Text(
                      'الإجازات',
                      style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    TextButton.icon(
                      onPressed: _saving ? null : _addLeave,
                      icon: const Icon(Icons.add),
                      label: const Text('إضافة إجازة'),
                    ),
                  ],
                ),
                if (_leaves.isEmpty) const Text('لا توجد إجازات مسجلة'),
                for (final leave in _leaves)
                  Card(
                    child: ListTile(
                      title: Text(
                        '${leave['type']} • ${leave['status'] == 'approved' ? 'معتمدة' : 'ملغاة'}',
                      ),
                      subtitle: Text(
                        '${dateLabel(workDate(leave['start']))} — ${dateLabel(workDate(leave['end']))}',
                      ),
                      trailing: leave['status'] == 'approved'
                          ? IconButton(
                              tooltip: 'إلغاء الإجازة',
                              onPressed: _saving
                                  ? null
                                  : () => setState(
                                      () => leave['status'] = 'cancelled',
                                    ),
                              icon: const Icon(Icons.event_busy),
                            )
                          : null,
                    ),
                  ),
                const SizedBox(height: 16),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _archived,
                  onChanged: _saving
                      ? null
                      : (value) => setState(() => _archived = value),
                  title: const Text('أرشفة الملف الإداري'),
                  subtitle: const Text(
                    'توقف متابعة وثائقه في اللوحة والتنبيهات؛ لا تُوقف حساب الدخول.',
                  ),
                ),
                if (_error != null)
                  Text(
                    _error!,
                    style: const TextStyle(color: Colors.orangeAccent),
                  ),
                const SizedBox(height: 16),
                FilledButton.icon(
                  onPressed: _saving ? null : _save,
                  icon: const Icon(Icons.save_outlined),
                  label: Text(_saving ? 'جارٍ الحفظ…' : 'حفظ الملف'),
                ),
                const SizedBox(height: 24),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _LeaveDialog extends StatefulWidget {
  final List<dynamic> existing;
  const _LeaveDialog({required this.existing});
  @override
  State<_LeaveDialog> createState() => _LeaveDialogState();
}

class _LeaveDialogState extends State<_LeaveDialog> {
  DateTime? _start, _end;
  String _type = 'سنوية';
  String? _error;
  Future<void> _pick(bool start) async {
    final date = await showDatePicker(
      context: context,
      initialDate: (start ? _start : _end ?? _start) ?? DateTime.now(),
      firstDate: DateTime(1900),
      lastDate: DateTime(2200),
    );
    if (date != null && mounted) {
      setState(() {
        if (start) {
          _start = date;
        } else {
          _end = date;
        }
      });
    }
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: AlertDialog(
      title: const Text('تسجيل إجازة معتمدة'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            DropdownButtonFormField<String>(
              initialValue: _type,
              items: [
                'سنوية',
                'مرضية',
                'بدون راتب',
                'أخرى',
              ].map((e) => DropdownMenuItem(value: e, child: Text(e))).toList(),
              onChanged: (v) => setState(() => _type = v!),
            ),
            ListTile(
              title: const Text('من'),
              subtitle: Text(dateLabel(_start)),
              onTap: () => _pick(true),
            ),
            ListTile(
              title: const Text('إلى'),
              subtitle: Text(dateLabel(_end)),
              onTap: () => _pick(false),
            ),
            if (_error != null)
              Text(_error!, style: const TextStyle(color: Colors.orangeAccent)),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('إلغاء'),
        ),
        FilledButton(
          onPressed: () {
            final error = validateLeave(
              _start,
              _end,
              widget.existing,
              'approved',
            );
            if (error != null) {
              setState(() => _error = error);
              return;
            }
            Navigator.pop(context, {
              'id': DateTime.now().microsecondsSinceEpoch.toString(),
              'type': _type,
              'start': _start!.toIso8601String(),
              'end': _end!.toIso8601String(),
              'status': 'approved',
            });
          },
          child: const Text('إضافة'),
        ),
      ],
    ),
  );
}
