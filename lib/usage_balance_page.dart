import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'branch_store.dart';
import 'cloud_api.dart';

class UsageBalancePage extends StatefulWidget {
  const UsageBalancePage({super.key});

  @override
  State<UsageBalancePage> createState() => _UsageBalancePageState();
}

class _UsageBalancePageState extends State<UsageBalancePage> {
  Map<String, dynamic>? _summary;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    final prefs = await BranchPreferences.getInstance();
    final token = await const FlutterSecureStorage().read(
      key: 'cloud_session_token',
    );
    if (token == null || token.isEmpty) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = 'سجّل الدخول أولًا لعرض رصيدك.';
        });
      }
      return;
    }
    final api = KhdoomCloudApi(
      scope: prefs,
      baseUrl:
          prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com',
    )..token = token;
    try {
      final data = await api.usageSummary();
      if (mounted) {
        setState(() {
          _summary = data;
          _loading = false;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = error is CloudApiException
              ? error.message
              : 'تعذر تحميل الرصيد. تأكد من الاتصال ثم أعد المحاولة.';
        });
      }
    } finally {
      api.close();
    }
  }

  int _number(Object? value) =>
      value is num ? value.toInt() : int.tryParse('$value') ?? 0;

  @override
  Widget build(BuildContext context) {
    final data = _summary ?? const <String, dynamic>{};
    final percent = _number(data['usagePercent']).clamp(0, 100);
    final services = Map<String, dynamic>.from(data['services'] as Map? ?? {});
    return Directionality(
      textDirection: TextDirection.rtl,
      child: Scaffold(
        backgroundColor: const Color(0xFF0B1020),
        appBar: AppBar(
          backgroundColor: const Color(0xFF111B35),
          foregroundColor: Colors.white,
          centerTitle: true,
          title: const Text(
            'استخدامي / الرصيد',
            style: TextStyle(fontWeight: FontWeight.bold),
          ),
          actions: [
            IconButton(onPressed: _load, icon: const Icon(Icons.refresh)),
          ],
        ),
        body: _loading
            ? const Center(
                child: CircularProgressIndicator(color: Color(0xFF38BDF8)),
              )
            : _error != null
            ? _Message(text: _error!, onRetry: _load)
            : RefreshIndicator(
                onRefresh: _load,
                color: const Color(0xFF38BDF8),
                child: ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    _OverviewCard(
                      packageName:
                          const {
                            'free': 'المجانية',
                            'basic': 'الأساسية',
                            'vip': 'VIP',
                          }[data['package']?.toString()] ??
                          'المجانية',
                      percent: percent,
                      used: _number(data['usageMonth']),
                      remaining: _number(data['totalRemaining']),
                      renewalDate:
                          data['renewalDate']?.toString() ?? 'غير محدد',
                    ),
                    const SizedBox(height: 14),
                    ...services.entries.map(
                      (entry) => _ServiceCard(
                        data: Map<String, dynamic>.from(entry.value as Map),
                        number: _number,
                      ),
                    ),
                    const SizedBox(height: 10),
                    const Text(
                      'الرصيد يتجدد كل شهر من تاريخ اشتراكك. عند انتهاء رصيد خدمة تتوقف حتى التجديد أو ترقية الباقة.',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.white54),
                    ),
                  ],
                ),
              ),
      ),
    );
  }
}

class _OverviewCard extends StatelessWidget {
  final String packageName;
  final int percent;
  final int used;
  final int remaining;
  final String renewalDate;
  const _OverviewCard({
    required this.packageName,
    required this.percent,
    required this.used,
    required this.remaining,
    required this.renewalDate,
  });

  @override
  Widget build(BuildContext context) => Card(
    color: const Color(0xFF172554),
    shape: RoundedRectangleBorder(
      borderRadius: BorderRadius.circular(20),
      side: const BorderSide(color: Color(0xFF38BDF8)),
    ),
    child: Padding(
      padding: const EdgeInsets.all(18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Icon(
                Icons.account_balance_wallet_outlined,
                color: Color(0xFF67E8F9),
              ),
              const SizedBox(width: 8),
              Text(
                'الباقة $packageName',
                style: const TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 18,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
          Text(
            'استهلاك باقتك في هذه الدورة: $percent%',
            style: const TextStyle(color: Colors.white, fontSize: 16),
          ),
          const SizedBox(height: 8),
          LinearProgressIndicator(
            value: percent / 100,
            minHeight: 9,
            borderRadius: BorderRadius.circular(8),
            backgroundColor: Colors.white12,
            color: percent >= 80
                ? Colors.orangeAccent
                : const Color(0xFF38BDF8),
          ),
          const SizedBox(height: 14),
          Text(
            'المتبقي: $remaining وحدة  •  المستهلك: $used وحدة',
            style: const TextStyle(color: Colors.white70),
          ),
          const SizedBox(height: 6),
          Text(
            'يتجدد الرصيد يوم: $renewalDate',
            style: const TextStyle(color: Colors.white54),
          ),
        ],
      ),
    ),
  );
}

class _ServiceCard extends StatelessWidget {
  final Map<String, dynamic> data;
  final int Function(Object?) number;
  const _ServiceCard({required this.data, required this.number});

  @override
  Widget build(BuildContext context) {
    final unlimited = data['unlimited'] == true;
    final remaining = number(data['remaining']);
    final used = number(data['usedMonth']);
    final limit = number(data['limit']);
    final percent = number(data['usagePercent']).clamp(0, 100);
    final dailyStop = data['exhausted'] == true && data['reason'] == 'daily';
    final notIncluded = !unlimited && limit <= 0;
    final empty = !unlimited && !notIncluded && !dailyStop && remaining <= 0;
    final warning =
        !unlimited && !notIncluded && (empty || dailyStop || percent >= 80);
    final dailyLimit = data['dailyLimit'];
    return Card(
      color: const Color(0xFF111B35),
      margin: const EdgeInsets.only(bottom: 12),
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: BorderSide(color: warning ? Colors.orangeAccent : Colors.white12),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  empty ? Icons.error_outline : Icons.check_circle_outline,
                  color: warning ? Colors.orangeAccent : Colors.greenAccent,
                ),
                const SizedBox(width: 8),
                Text(
                  data['label']?.toString() ?? 'خدمة',
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                    fontSize: 17,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            if (notIncluded)
              const Text(
                'غير مشمولة في باقتك الحالية',
                style: TextStyle(color: Colors.white70),
              )
            else if (unlimited) ...[
              const Text(
                'بلا حد في باقتك',
                style: TextStyle(color: Colors.white),
              ),
              Text(
                'المستخدم في هذه الدورة: $used',
                style: const TextStyle(color: Colors.white70),
              ),
            ] else ...[
              Text(
                'المستخدم: $used من $limit',
                style: const TextStyle(color: Colors.white),
              ),
              const SizedBox(height: 8),
              LinearProgressIndicator(
                value: percent / 100,
                minHeight: 7,
                borderRadius: BorderRadius.circular(8),
                backgroundColor: Colors.white12,
                color: warning ? Colors.orangeAccent : const Color(0xFF38BDF8),
              ),
              const SizedBox(height: 8),
              Text(
                'المتبقي: $remaining',
                style: const TextStyle(color: Colors.white70),
              ),
            ],
            if (dailyLimit is num)
              Text(
                'اليوم: ${number(data['usedToday'])} من ${dailyLimit.toInt()}',
                style: const TextStyle(color: Colors.white70),
              ),
            if (empty)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text(
                  'تم استخدام رصيد هذه الخدمة. تتوقف حتى يتجدد الرصيد أو ترقّي باقتك، والخدمات الأخرى مستقلة.',
                  style: TextStyle(color: Colors.orangeAccent),
                ),
              )
            else if (dailyStop)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text(
                  'وصلت السقف اليومي لهذه الخدمة. تعود غدًا، وباقي رصيد الشهر محفوظ.',
                  style: TextStyle(color: Colors.orangeAccent),
                ),
              )
            else if (warning)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text(
                  'الرصيد قارب على الانتهاء ⚠️',
                  style: TextStyle(color: Colors.orangeAccent),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _Message extends StatelessWidget {
  final String text;
  final VoidCallback onRetry;
  const _Message({required this.text, required this.onRetry});
  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            text,
            textAlign: TextAlign.center,
            style: const TextStyle(color: Colors.white70),
          ),
          const SizedBox(height: 12),
          FilledButton(onPressed: onRetry, child: const Text('إعادة المحاولة')),
        ],
      ),
    ),
  );
}
