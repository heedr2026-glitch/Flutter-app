import 'commercial_research_models.dart';

abstract class CommercialLeadStore {
  Future<List<CommercialLead>> listForOrganization(String organizationId);
  Future<CommercialLead?> find(String organizationId, String leadId);
  Future<void> save(CommercialLead lead);
}

abstract class CommercialOutboundGateway {
  Future<void> sendWhatsApp({
    required CommercialLead lead,
    required CommercialContact contact,
    required CommercialMessage message,
  });
}

abstract class CommercialApprovalNotifier {
  Future<void> notifyPriceApprovalRequired({
    required CommercialLead lead,
    required CommercialPriceDraft draft,
  });
}

class PriceApprovalRequiredException implements Exception {
  const PriceApprovalRequiredException();

  @override
  String toString() => 'يجب اعتماد السعر قبل إرساله للعميل.';
}

/// ينسق الحفظ والتواصل، ويمنع إرسال الأسعار دون موافقة حتى لو حاولت الواجهة ذلك.
class CommercialResearchService {
  const CommercialResearchService({
    required this.store,
    required this.outbound,
    required this.notifier,
  });

  final CommercialLeadStore store;
  final CommercialOutboundGateway outbound;
  final CommercialApprovalNotifier notifier;

  Future<void> saveLead(CommercialLead lead) => store.save(lead);

  Future<void> requestPriceApproval({
    required CommercialLead lead,
    required CommercialPriceDraft draft,
  }) async {
    final pending = draft.copyWith(status: PriceApprovalStatus.pending);
    await notifier.notifyPriceApprovalRequired(lead: lead, draft: pending);
  }

  Future<void> sendMessage({
    required CommercialLead lead,
    required CommercialContact contact,
    required CommercialMessage message,
    CommercialPriceDraft? approvedDraft,
  }) async {
    if (!CommercialPolicy.maySendMessage(
      message: message,
      approvedDraft: approvedDraft,
    )) {
      throw const PriceApprovalRequiredException();
    }

    await outbound.sendWhatsApp(lead: lead, contact: contact, message: message);
  }
}
