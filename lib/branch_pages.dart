import 'package:flutter/material.dart';

import 'branch_store.dart';
import 'cloud_api.dart';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'page_refresh_button.dart';
import 'khdoom_dark_page.dart';

class BranchManagementPage extends StatefulWidget {
  const BranchManagementPage({super.key});
  @override
  State<BranchManagementPage> createState() => _BranchManagementPageState();
}

class _BranchManagementPageState extends State<BranchManagementPage> {
  BranchPreferences? _prefs;
  final _name = TextEditingController();
  String? _error;
  bool _saving = false;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await BranchPreferences.getInstance();
    if (mounted) setState(() => _prefs = prefs);
  }

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _add() async {
    if (_prefs == null || _saving) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final api = KhdoomCloudApi(
        scope: _prefs,
        baseUrl:
            _prefs!.raw.getString('cloud_api_url') ??
            'https://khdoom-api.onrender.com',
      );
      try {
        api.token = await const FlutterSecureStorage().read(
          key: 'cloud_session_token',
        );
        if (api.token == null)
          throw const CloudApiException(401, 'سجل الدخول بالخادم لإضافة فرع');
        final quote = await api.branchQuote();
        if (!mounted) return;
        final accepted = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('رسوم الفرع الإضافي'),
            content: Text(
              'الرسوم الشهرية الحالية: ${quote['amount']} ريال.\n${quote['benefit_ends'] == null ? '' : 'ينتهي استحقاق العرض في ${quote['benefit_ends']}.'}\nالفرع المدفوع يحتاج مراجعة الرسوم قبل التفعيل.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('إلغاء'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('تأكيد الطلب'),
              ),
            ],
          ),
        );
        if (accepted != true) return;
        final branch = await api.createCloudBranch({
          'id': 'b${DateTime.now().microsecondsSinceEpoch}',
          'name': _name.text.trim(),
          'period': 'monthly',
          'quote_id': quote['quote_id'],
        });
        if (branch['status'] != 'active')
          throw const CloudApiException(
            409,
            'حُفظ طلب الفرع وبانتظار مراجعة الإدارة قبل تفعيله',
          );
        await _prefs!.addBranch(
          _name.text,
          registeredId: branch['id'] as String,
        );
      } finally {
        api.close();
      }
      if (!mounted) return;
      _name.clear();
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('تمت إضافة الفرع. اضغط على اسمه أدناه للدخول إليه.'),
        ),
      );
    } catch (error) {
      if (mounted)
        setState(
          () => _error = error is ArgumentError
              ? error.message.toString()
              : error is CloudApiException
              ? error.message
              : 'تعذر حفظ الفرع، حاول مرة أخرى',
        );
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _select(String id) async {
    final prefs = _prefs;
    if (prefs == null || _saving || prefs.branchId == id) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await prefs.selectBranch(id);
      await _load();
      if (!mounted) return;
      if (Navigator.of(context).canPop()) {
        Navigator.of(context).pop(true);
      }
    } catch (_) {
      if (mounted) setState(() => _error = 'تعذر اختيار الفرع، حاول مرة أخرى');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => KhdoomDarkPage(
    child: Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('إدارة الفروع'),
          actions: [PageRefreshButton(onRefresh: _load)],
        ),
        body: _prefs == null
            ? const Center(child: CircularProgressIndicator())
            : _prefs!.raw.getString('session_user_type') == 'employee'
            ? const Center(child: Text('إدارة الفروع متاحة للمدير فقط'))
            : ListView(
                padding: const EdgeInsets.all(20),
                children: [
                  const Text(
                    'كل فرع له سجلاته وموظفوه ومركباته ومواعيده. بياناتك السابقة تبقى في المقر الرئيسي.',
                  ),
                  const SizedBox(height: 12),
                  const Text(
                    'سجل الفروع ورسومها مرتبطان بالخادم. تبقى السجلات المحلية محفوظة على الجهاز حتى تتم مزامنتها.',
                  ),
                  const SizedBox(height: 20),
                  TextField(
                    controller: _name,
                    maxLength: 80,
                    enabled: !_saving,
                    decoration: InputDecoration(
                      labelText: 'اسم الفرع الجديد',
                      hintText: 'مثال: فرع الرياض',
                      errorText: _error,
                    ),
                    onSubmitted: (_) => _add(),
                  ),
                  FilledButton.icon(
                    onPressed: _saving ? null : _add,
                    icon: const Icon(Icons.add_business),
                    label: Text(_saving ? 'جارٍ الحفظ…' : 'إضافة فرع'),
                  ),
                  const SizedBox(height: 20),
                  for (final branch in BranchPreferences.branches(_prefs!.raw))
                    Card(
                      child: ListTile(
                        leading: Icon(
                          branch['id'] == BranchPreferences.mainId
                              ? Icons.business
                              : Icons.store,
                        ),
                        title: Text(branch['name']!),
                        subtitle: Text(
                          branch['id'] == _prefs!.branchId
                              ? 'الفرع المختار حاليًا'
                              : 'اضغط للدخول إلى هذا الفرع',
                        ),
                        selected: branch['id'] == _prefs!.branchId,
                        trailing: Icon(
                          branch['id'] == _prefs!.branchId
                              ? Icons.check_circle
                              : Icons.login,
                        ),
                        onTap: _saving || branch['id'] == _prefs!.branchId
                            ? null
                            : () => _select(branch['id']!),
                      ),
                    ),
                ],
              ),
      ),
    ),
  );
}

class BranchSelector extends StatefulWidget {
  final VoidCallback onChanged;
  final bool compact;
  const BranchSelector({
    super.key,
    required this.onChanged,
    this.compact = false,
  });
  @override
  State<BranchSelector> createState() => _BranchSelectorState();
}

class _BranchSelectorState extends State<BranchSelector> {
  BranchPreferences? _prefs;
  bool _switching = false;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final prefs = await BranchPreferences.getInstance();
    if (mounted) setState(() => _prefs = prefs);
  }

  Future<void> _choose() async {
    if (_switching) return;
    final prefs = await BranchPreferences.getInstance();
    if (!mounted || prefs.raw.getString('session_user_type') == 'employee')
      return;
    final selected = await showModalBottomSheet<String>(
      context: context,
      builder: (sheetContext) => Directionality(
        textDirection: TextDirection.rtl,
        child: SafeArea(
          child: ListView(
            shrinkWrap: true,
            children: [
              const ListTile(title: Text('اختر الفرع')),
              for (final branch in BranchPreferences.branches(prefs.raw))
                ListTile(
                  leading: Icon(
                    branch['id'] == prefs.branchId
                        ? Icons.radio_button_checked
                        : Icons.radio_button_off,
                  ),
                  title: Text(branch['name']!),
                  onTap: () => Navigator.pop(sheetContext, branch['id']),
                ),
              ListTile(
                leading: const Icon(Icons.add_business),
                title: const Text('إضافة فرع / إدارة الفروع'),
                onTap: () => Navigator.pop(sheetContext, 'manage'),
              ),
            ],
          ),
        ),
      ),
    );
    if (!mounted || selected == null) return;
    if (selected == 'manage') {
      final changed = await Navigator.push<bool>(
        context,
        MaterialPageRoute(builder: (_) => const BranchManagementPage()),
      );
      await _load();
      if (mounted && changed == true) widget.onChanged();
      return;
    }
    if (selected == prefs.branchId) return;
    setState(() => _switching = true);
    try {
      await prefs.selectBranch(selected);
      if (mounted) widget.onChanged();
    } catch (_) {
      if (mounted)
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('تعذر اختيار الفرع')));
    } finally {
      if (mounted) setState(() => _switching = false);
    }
  }

  @override
  Widget build(BuildContext context) => widget.compact
      ? SizedBox(
          width: 96,
          child: Material(
            color: const Color(0xFF111B35),
            borderRadius: BorderRadius.circular(18),
            child: InkWell(
              borderRadius: BorderRadius.circular(18),
              onTap:
                  _switching ||
                      _prefs == null ||
                      _prefs!.raw.getString('session_user_type') == 'employee'
                  ? null
                  : _choose,
              child: Container(
                constraints: const BoxConstraints(minHeight: 105),
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: const Color(0xFF38BDF8)),
                ),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    const Icon(
                      Icons.account_tree_outlined,
                      color: Color(0xFF67E8F9),
                      size: 25,
                    ),
                    const Text(
                      'الفروع',
                      style: TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 16,
                      ),
                    ),
                    Text(
                      _prefs?.branchName ?? 'المقر الرئيسي',
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: Colors.white70,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        )
      : Card(
          child: ListTile(
            leading: const Icon(Icons.account_tree_outlined),
            title: Text(_prefs?.branchName ?? 'المقر الرئيسي'),
            subtitle: const Text('التصنيفات والمعلومات تخص الفرع المختار'),
            trailing: _prefs?.raw.getString('session_user_type') == 'employee'
                ? null
                : const Icon(Icons.expand_more),
            onTap:
                _switching ||
                    _prefs == null ||
                    _prefs!.raw.getString('session_user_type') == 'employee'
                ? null
                : _choose,
          ),
        );
}

class CategoriesBranchActions extends StatelessWidget {
  final VoidCallback onCategories;
  final VoidCallback onBranchChanged;
  final bool showBranches;
  final bool compactHeight;
  const CategoriesBranchActions({
    super.key,
    required this.onCategories,
    required this.onBranchChanged,
    this.showBranches = false,
    this.compactHeight = false,
  });
  @override
  Widget build(BuildContext context) => Row(
    children: [
      Expanded(
        child: Material(
          color: const Color(0xFF111B35),
          borderRadius: BorderRadius.circular(18),
          child: InkWell(
            onTap: onCategories,
            borderRadius: BorderRadius.circular(18),
            child: Container(
              constraints: BoxConstraints(minHeight: compactHeight ? 90 : 105),
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(18),
                border: Border.all(color: const Color(0xFF38BDF8), width: 1.5),
              ),
              child: const Center(
                child: FittedBox(
                  fit: BoxFit.scaleDown,
                  child: Row(
                    children: [
                      Icon(
                        Icons.category_outlined,
                        color: Color(0xFF67E8F9),
                        size: 30,
                      ),
                      SizedBox(width: 8),
                      Text(
                        'التصنيفات',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 27,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
      if (showBranches) ...[
        const SizedBox(width: 10),
        BranchSelector(compact: true, onChanged: onBranchChanged),
      ],
    ],
  );
}
