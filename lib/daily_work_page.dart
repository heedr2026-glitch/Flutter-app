import 'package:flutter/material.dart';

import 'branch_store.dart';
import 'daily_work_store.dart';
import 'khdoom_dark_page.dart';

class DailyWorkPage extends StatefulWidget {
  final Map<String, Widget Function(BuildContext, DailyWorkItem)> destinations;
  const DailyWorkPage({super.key, required this.destinations});
  @override
  State<DailyWorkPage> createState() => _DailyWorkPageState();
}

class _DailyWorkPageState extends State<DailyWorkPage> {
  DailyWorkStore? _store;
  List<DailyWorkItem> _items = [];
  List<DailyWorkItem> _hidden = [];
  List<Map<String, dynamic>> _done = [];
  String? _error;
  String _filter = 'all';
  bool _busy = false;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final store =
          _store ?? DailyWorkStore(await BranchPreferences.getInstance());
      final all = store.agenda(DateTime.now(), includeHidden: true);
      final items = all.where((item) => !store.isHidden(item)).toList();
      final hidden = all.where(store.isHidden).toList();
      final done = store
          .records(DailyWorkStore.tasksKey)
          .where((e) => e['done'] == true)
          .toList();
      if (mounted) {
        setState(() {
          _store = store;
          _items = items;
          _hidden = hidden;
          _done = done;
          _error = null;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(
          () => _error = 'تعذر تحميل اللوحة أو لا تملك صلاحية صاحب المؤسسة',
        );
      }
    }
  }

  Future<void> _editTask([Map<String, dynamic>? initial]) async {
    final task = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (_) => _TaskDialog(
        initial: initial,
        employees: _store!
            .records(DailyWorkStore.employeesKey)
            .where((e) => e['archived'] != true)
            .toList(),
      ),
    );
    if (task == null || !mounted) return;
    await _mutate(() => _store!.saveRecord(DailyWorkStore.tasksKey, task));
  }

  Future<void> _mutate(Future<void> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
      await _load();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('تعذر حفظ التغيير، حاول مرة أخرى')),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _open(DailyWorkItem item) async {
    if (item.task != null) {
      await _editTask(item.task);
      return;
    }
    final builder = widget.destinations[item.destination];
    if (builder == null) return;
    await Navigator.push<void>(
      context,
      MaterialPageRoute(builder: (context) => builder(context, item)),
    );
    await _load();
  }

  Future<void> _remove({
    DailyWorkItem? item,
    Map<String, dynamic>? task,
  }) async {
    final record = task ?? item?.task;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => KhdoomDarkPage(
        child: Directionality(
          textDirection: TextDirection.rtl,
          child: AlertDialog(
            title: Text(
              record == null ? 'إزالة التنبيه من اللوحة' : 'حذف المهمة',
            ),
            content: Text(
              record == null
                  ? 'سيُزال «${item!.title}» من اللوحة فقط. يبقى السجل الأصلي وإشعارات الجهاز، ويمكن استعادته من قسم المُزالة. إذا تغير الاستحقاق سيظهر مجددًا.'
                  : 'حذف «${record['title']}»؟ لا يؤثر ذلك على المركبة أو المستند المرتبط.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext, false),
                child: const Text('إلغاء'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(dialogContext, true),
                child: Text(record == null ? 'إزالة من اللوحة' : 'حذف'),
              ),
            ],
          ),
        ),
      ),
    );
    if (confirmed != true || !mounted) return;
    await _mutate(
      () => record == null
          ? _store!.setHidden(item!, true)
          : _store!.deleteTask(record['id'].toString()),
    );
  }

  int? _days(DailyWorkItem item) =>
      item.due == null ? null : daysFrom(item.due!, DateTime.now());
  bool _matches(DailyWorkItem item) => switch (_filter) {
    'late' => (_days(item) ?? 0) < 0,
    'today' => _days(item) == 0 || item.due == null,
    'soon' => (_days(item) ?? 0) > 0,
    _ => true,
  };
  @override
  Widget build(BuildContext context) => KhdoomDarkPage(
    child: Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('لوحتي اليومية'),
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
                onPressed: _busy ? null : () => _editTask(),
                icon: const Icon(Icons.add_task),
                label: const Text('إضافة مهمة'),
              ),
        body: _error != null
            ? Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(_error!),
                    TextButton(
                      onPressed: _load,
                      child: const Text('إعادة المحاولة'),
                    ),
                  ],
                ),
              )
            : _store == null
            ? const Center(child: CircularProgressIndicator())
            : RefreshIndicator(
                onRefresh: _load,
                child: ListView(
                  padding: const EdgeInsets.fromLTRB(16, 16, 16, 100),
                  children: [
                    Text(
                      'وش عندي اليوم؟',
                      style: Theme.of(context).textTheme.headlineSmall
                          ?.copyWith(
                            color: Colors.white,
                            fontWeight: FontWeight.bold,
                          ),
                    ),
                    Text(
                      '${_store!.prefs.branchName} • ${dateLabel(DateTime.now())}',
                      style: const TextStyle(color: Colors.white70),
                    ),
                    const SizedBox(height: 12),
                    const Text(
                      'من آخر بيانات محفوظة على الجهاز. افتح المواعيد لتحديث طلبات العملاء من السحابة.',
                      style: TextStyle(color: Colors.white60),
                    ),
                    const SizedBox(height: 12),
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        for (final entry in {
                          'all': 'الكل (${_items.length})',
                          'late':
                              'متأخر (${_items.where((e) => (_days(e) ?? 0) < 0).length})',
                          'today':
                              'اليوم (${_items.where((e) => _days(e) == 0 || e.due == null).length})',
                          'soon':
                              'قادم (${_items.where((e) => (_days(e) ?? 0) > 0).length})',
                          'done': 'مهام منجزة (${_done.length})',
                          'hidden': 'المُزالة (${_hidden.length})',
                        }.entries)
                          ChoiceChip(
                            label: Text(entry.value),
                            selected: _filter == entry.key,
                            onSelected: (_) =>
                                setState(() => _filter = entry.key),
                          ),
                      ],
                    ),
                    const SizedBox(height: 16),
                    if (_filter == 'hidden') ...[
                      if (_hidden.isEmpty)
                        const Padding(
                          padding: EdgeInsets.all(24),
                          child: Text('لا توجد تنبيهات مُزالة'),
                        ),
                      for (final item in _hidden)
                        Card(
                          child: ListTile(
                            title: Text(item.title),
                            trailing: TextButton(
                              onPressed: _busy
                                  ? null
                                  : () => _mutate(
                                      () => _store!.setHidden(item, false),
                                    ),
                              child: const Text('استعادة'),
                            ),
                          ),
                        ),
                    ] else if (_filter == 'done') ...[
                      if (_done.isEmpty)
                        const Padding(
                          padding: EdgeInsets.all(24),
                          child: Text('لا توجد مهام منجزة بعد'),
                        ),
                      for (final task in _done)
                        Card(
                          child: ListTile(
                            title: Text(task['title'].toString()),
                            subtitle: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(task['assignee']?.toString() ?? ''),
                                Wrap(
                                  children: [
                                    TextButton(
                                      onPressed: _busy
                                          ? null
                                          : () => _mutate(
                                              () => _store!.setTaskDone(
                                                task,
                                                false,
                                              ),
                                            ),
                                      child: const Text('إعادة فتح'),
                                    ),
                                    TextButton(
                                      onPressed: _busy
                                          ? null
                                          : () => _remove(task: task),
                                      child: const Text('حذف المهمة'),
                                    ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                        ),
                    ] else ...[
                      if (!_items.any(_matches))
                        const Padding(
                          padding: EdgeInsets.all(24),
                          child: Text(
                            'لا توجد عناصر في هذا القسم. أضف مهمة أو سجّل وثائق المؤسسة والموظفين.',
                            textAlign: TextAlign.center,
                          ),
                        ),
                      for (final item in _items.where(_matches)) _card(item),
                    ],
                  ],
                ),
              ),
      ),
    ),
  );
  Widget _card(DailyWorkItem item) {
    final days = _days(item);
    final color = days != null && days < 0
        ? Colors.redAccent
        : days == 0
        ? Colors.amber
        : const Color(0xFF7DD3FC);
    final status = days == null
        ? 'يحتاج مراجعة'
        : days < 0
        ? 'متأخر ${-days} يوم'
        : days == 0
        ? 'اليوم'
        : 'خلال $days يوم';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  item.task == null
                      ? Icons.notifications_active_outlined
                      : Icons.task_alt,
                  color: color,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    item.title,
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              '$status • ${dateLabel(item.due)}',
              style: TextStyle(color: color),
            ),
            if (item.detail.trim().isNotEmpty)
              Text(item.detail, style: const TextStyle(color: Colors.white70)),
            Wrap(
              spacing: 8,
              children: [
                TextButton.icon(
                  onPressed: _busy ? null : () => _open(item),
                  icon: const Icon(Icons.open_in_new),
                  label: Text(
                    item.task == null ? 'فتح ومتابعة' : 'تعديل المهمة',
                  ),
                ),
                TextButton.icon(
                  onPressed: _busy ? null : () => _remove(item: item),
                  icon: const Icon(Icons.delete_outline),
                  label: Text(
                    item.task == null ? 'إزالة من اللوحة' : 'حذف المهمة',
                  ),
                ),
                if (item.task != null)
                  TextButton.icon(
                    onPressed: _busy
                        ? null
                        : () => _mutate(
                            () => _store!.setTaskDone(item.task!, true),
                          ),
                    icon: const Icon(Icons.check_circle_outline),
                    label: const Text('تم الإنجاز'),
                  )
                else
                  TextButton.icon(
                    onPressed: _busy
                        ? null
                        : () => _editTask({
                            'title': 'متابعة: ${item.title}',
                            'due': (item.due ?? DateTime.now())
                                .toIso8601String(),
                            'notes': item.detail,
                          }),
                    icon: const Icon(Icons.person_add_alt),
                    label: const Text('تكليف بمتابعة'),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _TaskDialog extends StatefulWidget {
  final Map<String, dynamic>? initial;
  final List<Map<String, dynamic>> employees;
  const _TaskDialog({this.initial, required this.employees});
  @override
  State<_TaskDialog> createState() => _TaskDialogState();
}

class _TaskDialogState extends State<_TaskDialog> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _title, _assignee, _notes;
  late DateTime _due;
  @override
  void initState() {
    super.initState();
    _title = TextEditingController(
      text: widget.initial?['title']?.toString() ?? '',
    );
    _assignee = TextEditingController(
      text: widget.initial?['assignee']?.toString() ?? 'صاحب المؤسسة',
    );
    _notes = TextEditingController(
      text: widget.initial?['notes']?.toString() ?? '',
    );
    _due = workDate(widget.initial?['due']) ?? dayOnly(DateTime.now());
  }

  @override
  void dispose() {
    _title.dispose();
    _assignee.dispose();
    _notes.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Directionality(
    textDirection: TextDirection.rtl,
    child: AlertDialog(
      title: Text(
        widget.initial?['id'] == null ? 'مهمة متابعة جديدة' : 'تعديل المهمة',
      ),
      content: SingleChildScrollView(
        child: Form(
          key: _form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                controller: _title,
                decoration: const InputDecoration(labelText: 'المطلوب'),
                validator: (v) =>
                    (v ?? '').trim().isEmpty ? 'اكتب المهمة' : null,
              ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _assignee,
                decoration: const InputDecoration(labelText: 'المسؤول'),
                validator: (v) =>
                    (v ?? '').trim().isEmpty ? 'حدد المسؤول' : null,
              ),
              if (widget.employees.isNotEmpty)
                DropdownButtonFormField<String>(
                  isExpanded: true,
                  decoration: const InputDecoration(
                    labelText: 'اختر من ملفات الموظفين',
                  ),
                  items: widget.employees
                      .map(
                        (e) => DropdownMenuItem(
                          value: e['id'].toString(),
                          child: Text(
                            e['name'].toString(),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      )
                      .toList(),
                  onChanged: (id) {
                    _assignee.text = widget.employees
                        .firstWhere((e) => e['id'].toString() == id)['name']
                        .toString();
                  },
                ),
              const SizedBox(height: 12),
              TextFormField(
                controller: _notes,
                maxLines: 2,
                decoration: const InputDecoration(labelText: 'ملاحظات'),
              ),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('موعد التنفيذ'),
                subtitle: Text(dateLabel(_due)),
                trailing: const Icon(Icons.calendar_month),
                onTap: () async {
                  final date = await showDatePicker(
                    context: context,
                    initialDate: _due,
                    firstDate: DateTime(1900),
                    lastDate: DateTime(2200),
                  );
                  if (date != null && mounted) setState(() => _due = date);
                },
              ),
              const Text(
                'التكليف يُسجّل في اللوحة؛ لا يُرسل إشعارًا للموظف.',
                style: TextStyle(fontSize: 12),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('إلغاء'),
        ),
        FilledButton(
          onPressed: () {
            if (!_form.currentState!.validate()) return;
            Navigator.pop(context, {
              ...?widget.initial,
              'id':
                  widget.initial?['id'] ??
                  DateTime.now().microsecondsSinceEpoch.toString(),
              'title': _title.text.trim(),
              'assignee': _assignee.text.trim(),
              'notes': _notes.text.trim(),
              'due': _due.toIso8601String(),
              'done': widget.initial?['done'] ?? false,
            });
          },
          child: const Text('حفظ المهمة'),
        ),
      ],
    ),
  );
}
