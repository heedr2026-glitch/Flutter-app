import 'dart:convert';

/// حالة فرصة البيع التي اكتشفها موظف البحث التجاري.
enum CommercialLeadStatus {
  discovered,
  readyToContact,
  contacted,
  replied,
  needsSiteSurvey,
  priceRequested,
  pricePendingApproval,
  priceApproved,
  offerSent,
  won,
  lost,
}

enum CommercialMessageKind {
  introduction,
  followUp,
  requestDrawings,
  priceOffer,
}

enum PriceApprovalStatus { draft, pending, approved, rejected }

class CommercialSource {
  const CommercialSource({
    required this.title,
    required this.url,
    required this.collectedAt,
  });

  final String title;
  final String url;
  final DateTime collectedAt;

  Map<String, dynamic> toJson() => {
    'title': title,
    'url': url,
    'collectedAt': collectedAt.toIso8601String(),
  };

  factory CommercialSource.fromJson(Map<String, dynamic> json) =>
      CommercialSource(
        title: json['title'] as String? ?? '',
        url: json['url'] as String? ?? '',
        collectedAt:
            DateTime.tryParse(json['collectedAt'] as String? ?? '') ??
            DateTime.now(),
      );
}

class CommercialContact {
  const CommercialContact({
    this.name = '',
    this.company = '',
    this.role = '',
    this.phone = '',
    this.whatsApp = '',
  });

  final String name;
  final String company;
  final String role;
  final String phone;
  final String whatsApp;

  Map<String, dynamic> toJson() => {
    'name': name,
    'company': company,
    'role': role,
    'phone': phone,
    'whatsApp': whatsApp,
  };

  factory CommercialContact.fromJson(Map<String, dynamic> json) =>
      CommercialContact(
        name: json['name'] as String? ?? '',
        company: json['company'] as String? ?? '',
        role: json['role'] as String? ?? '',
        phone: json['phone'] as String? ?? '',
        whatsApp: json['whatsApp'] as String? ?? '',
      );
}

class CommercialMessage {
  const CommercialMessage({
    required this.id,
    required this.kind,
    required this.text,
    required this.createdAt,
    this.sentAt,
  });

  final String id;
  final CommercialMessageKind kind;
  final String text;
  final DateTime createdAt;
  final DateTime? sentAt;

  bool get wasSent => sentAt != null;
  bool get containsPrice => CommercialPolicy.containsPrice(text);

  Map<String, dynamic> toJson() => {
    'id': id,
    'kind': kind.name,
    'text': text,
    'createdAt': createdAt.toIso8601String(),
    'sentAt': sentAt?.toIso8601String(),
  };

  factory CommercialMessage.fromJson(Map<String, dynamic> json) =>
      CommercialMessage(
        id: json['id'] as String? ?? '',
        kind: CommercialMessageKind.values.firstWhere(
          (value) => value.name == json['kind'],
          orElse: () => CommercialMessageKind.introduction,
        ),
        text: json['text'] as String? ?? '',
        createdAt:
            DateTime.tryParse(json['createdAt'] as String? ?? '') ??
            DateTime.now(),
        sentAt: DateTime.tryParse(json['sentAt'] as String? ?? ''),
      );
}

class CommercialPriceDraft {
  const CommercialPriceDraft({
    required this.id,
    required this.amount,
    required this.currency,
    required this.details,
    required this.createdAt,
    this.status = PriceApprovalStatus.draft,
    this.reviewedAt,
    this.reviewNote = '',
  });

  final String id;
  final double amount;
  final String currency;
  final String details;
  final DateTime createdAt;
  final PriceApprovalStatus status;
  final DateTime? reviewedAt;
  final String reviewNote;

  bool get canBeSent => status == PriceApprovalStatus.approved;

  CommercialPriceDraft copyWith({
    PriceApprovalStatus? status,
    DateTime? reviewedAt,
    String? reviewNote,
  }) => CommercialPriceDraft(
    id: id,
    amount: amount,
    currency: currency,
    details: details,
    createdAt: createdAt,
    status: status ?? this.status,
    reviewedAt: reviewedAt ?? this.reviewedAt,
    reviewNote: reviewNote ?? this.reviewNote,
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'amount': amount,
    'currency': currency,
    'details': details,
    'createdAt': createdAt.toIso8601String(),
    'status': status.name,
    'reviewedAt': reviewedAt?.toIso8601String(),
    'reviewNote': reviewNote,
  };

  factory CommercialPriceDraft.fromJson(Map<String, dynamic> json) =>
      CommercialPriceDraft(
        id: json['id'] as String? ?? '',
        amount: (json['amount'] as num?)?.toDouble() ?? 0,
        currency: json['currency'] as String? ?? 'SAR',
        details: json['details'] as String? ?? '',
        createdAt:
            DateTime.tryParse(json['createdAt'] as String? ?? '') ??
            DateTime.now(),
        status: PriceApprovalStatus.values.firstWhere(
          (value) => value.name == json['status'],
          orElse: () => PriceApprovalStatus.draft,
        ),
        reviewedAt: DateTime.tryParse(json['reviewedAt'] as String? ?? ''),
        reviewNote: json['reviewNote'] as String? ?? '',
      );
}

class CommercialLead {
  const CommercialLead({
    required this.id,
    required this.organizationId,
    required this.projectName,
    required this.createdAt,
    required this.updatedAt,
    this.city = '',
    this.location = '',
    this.projectOwner = '',
    this.contractor = '',
    this.facadeType = '',
    this.estimatedMeasurements = '',
    this.measurementsAreEstimated = true,
    this.status = CommercialLeadStatus.discovered,
    this.contacts = const [],
    this.sources = const [],
    this.messages = const [],
    this.priceDrafts = const [],
    this.nextFollowUpAt,
    this.notes = '',
  });

  final String id;
  final String organizationId;
  final String projectName;
  final String city;
  final String location;
  final String projectOwner;
  final String contractor;
  final String facadeType;
  final String estimatedMeasurements;
  final bool measurementsAreEstimated;
  final CommercialLeadStatus status;
  final List<CommercialContact> contacts;
  final List<CommercialSource> sources;
  final List<CommercialMessage> messages;
  final List<CommercialPriceDraft> priceDrafts;
  final DateTime createdAt;
  final DateTime updatedAt;
  final DateTime? nextFollowUpAt;
  final String notes;

  Map<String, dynamic> toJson() => {
    'id': id,
    'organizationId': organizationId,
    'projectName': projectName,
    'city': city,
    'location': location,
    'projectOwner': projectOwner,
    'contractor': contractor,
    'facadeType': facadeType,
    'estimatedMeasurements': estimatedMeasurements,
    'measurementsAreEstimated': measurementsAreEstimated,
    'status': status.name,
    'contacts': contacts.map((item) => item.toJson()).toList(),
    'sources': sources.map((item) => item.toJson()).toList(),
    'messages': messages.map((item) => item.toJson()).toList(),
    'priceDrafts': priceDrafts.map((item) => item.toJson()).toList(),
    'createdAt': createdAt.toIso8601String(),
    'updatedAt': updatedAt.toIso8601String(),
    'nextFollowUpAt': nextFollowUpAt?.toIso8601String(),
    'notes': notes,
  };

  String encode() => jsonEncode(toJson());

  factory CommercialLead.decode(String value) =>
      CommercialLead.fromJson(jsonDecode(value) as Map<String, dynamic>);

  factory CommercialLead.fromJson(Map<String, dynamic> json) => CommercialLead(
    id: json['id'] as String? ?? '',
    organizationId: json['organizationId'] as String? ?? '',
    projectName: json['projectName'] as String? ?? '',
    city: json['city'] as String? ?? '',
    location: json['location'] as String? ?? '',
    projectOwner: json['projectOwner'] as String? ?? '',
    contractor: json['contractor'] as String? ?? '',
    facadeType: json['facadeType'] as String? ?? '',
    estimatedMeasurements: json['estimatedMeasurements'] as String? ?? '',
    measurementsAreEstimated: json['measurementsAreEstimated'] as bool? ?? true,
    status: CommercialLeadStatus.values.firstWhere(
      (value) => value.name == json['status'],
      orElse: () => CommercialLeadStatus.discovered,
    ),
    contacts: (json['contacts'] as List<dynamic>? ?? const [])
        .map((item) => CommercialContact.fromJson(item as Map<String, dynamic>))
        .toList(),
    sources: (json['sources'] as List<dynamic>? ?? const [])
        .map((item) => CommercialSource.fromJson(item as Map<String, dynamic>))
        .toList(),
    messages: (json['messages'] as List<dynamic>? ?? const [])
        .map((item) => CommercialMessage.fromJson(item as Map<String, dynamic>))
        .toList(),
    priceDrafts: (json['priceDrafts'] as List<dynamic>? ?? const [])
        .map(
          (item) => CommercialPriceDraft.fromJson(item as Map<String, dynamic>),
        )
        .toList(),
    createdAt:
        DateTime.tryParse(json['createdAt'] as String? ?? '') ?? DateTime.now(),
    updatedAt:
        DateTime.tryParse(json['updatedAt'] as String? ?? '') ?? DateTime.now(),
    nextFollowUpAt: DateTime.tryParse(json['nextFollowUpAt'] as String? ?? ''),
    notes: json['notes'] as String? ?? '',
  );
}

/// قاعدة أمان لا يجوز تجاوزها في الواجهة أو الخادم.
class CommercialPolicy {
  const CommercialPolicy._();

  static bool containsPrice(String text) {
    final normalized = text.replaceAll(',', '');
    final moneyPattern = RegExp(
      r'(\d+(?:\.\d+)?)\s*(?:ريال|ر\.س|SAR|SR)|(?:ريال|ر\.س|SAR|SR)\s*(\d+(?:\.\d+)?)',
      caseSensitive: false,
    );
    return moneyPattern.hasMatch(normalized);
  }

  static bool maySendMessage({
    required CommercialMessage message,
    CommercialPriceDraft? approvedDraft,
  }) {
    final isFinancial =
        message.kind == CommercialMessageKind.priceOffer ||
        message.containsPrice;
    if (!isFinancial) return true;
    return approvedDraft?.canBeSent == true;
  }
}
