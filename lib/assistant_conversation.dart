import 'dart:convert';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'branch_store.dart';
import 'cloud_api.dart';

/// Read-only conversation. Only safe counts leave the selected branch's device storage.
class AssistantConversation {
  final List<Map<String,String>> _history = [];
  bool busy = false;
  String? _branch;
  void bind(String branch) {
    if (_branch != branch) { _history.clear(); _branch=branch; }
  }

  Future<String> ask(BranchPreferences prefs, String question) async {
    bind(prefs.branchId);
    if (busy) return 'لحظة، أكمل جواب رسالتك السابقة أولًا.';
    if (prefs.getString('session_user_type') == 'employee') {
      return 'المحادثة الموسعة تحتاج صلاحية إدارة إعدادات المؤسسة. تقدر تستخدم الاستعلامات المتاحة لحسابك.';
    }
    busy = true;
    KhdoomCloudApi? api;
    try {
      const storage = FlutterSecureStorage();
      final token = await storage.read(key:'cloud_session_token');
      if (token == null || token.isEmpty) return 'سجل الدخول لتشغيل المحادثة الذكية وربطها بتعليم المؤسسة.';
      int count(String key) {
        try { final value=jsonDecode(prefs.getString(key) ?? '[]'); return value is List ? value.length : 0; }
        catch (_) { return 0; }
      }
      api=KhdoomCloudApi(scope:prefs,baseUrl:prefs.getString('cloud_api_url') ?? 'https://khdoom-api.onrender.com')..token=token;
      final response=await api.assistantConversation(question, _history, {
        'branchName':prefs.branchName,
        'vehicles':count('business_vehicles'),
        'employees':count('business_employees'),
        'appointments':count('business_appointments_requests'),
        'documents':count('organization_alert_records'),
      });
      final reply=response['text']?.toString() ?? 'لم يصل جواب من الخادم. حاول مجددًا.';
      remember(question,reply);
      return reply;
    } on CloudApiException catch (e) {
      return e.statusCode == 404 ? 'تحديث المحادثة الذكية لم يصل للخادم بعد. الاستعلامات المحلية ما زالت متاحة.' : e.message;
    } catch (_) {
      return 'تعذر الاتصال الآن. لم أنفّذ أو أحفظ أي إجراء؛ حاول مرة ثانية.';
    } finally { api?.close(); busy=false; }
  }

  void remember(String question,String answer) {
    if (_history.length>=2 && _history[_history.length-2]['message']==question && _history.last['message']==answer) return;
    _history.addAll([{'sender':'owner','message':question},{'sender':'assistant','message':answer}]);
    if (_history.length>12) _history.removeRange(0,_history.length-12);
  }
}
