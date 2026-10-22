"""Public notices for the current SupportOps AI service."""

import html


def render_policy(config, kind):
    operator = config.legal_business_name or "Kseniia Blokhina"
    country = config.legal_country or "Germany"
    contact = config.legal_contact_email or config.support_email
    identity = (
        f"SupportOps AI is operated by {operator}, {country}. Contact: {contact}."
    )
    if kind == "privacy":
        title = "Privacy notice"
        sections = [
            (
                "Who is responsible",
                identity
                + " This notice covers the service at app.xenonimus.com, including account registration and business workspaces.",
            ),
            (
                "Information we process",
                "We process account details such as your name, email address, password hash and email-verification status; business and team memberships; request content; uploaded policies and their versions; templates; AI results and their sources; review history; and usage records. Account security also uses session identifiers, IP-based rate limits and recovery tokens. Passwords and recovery tokens are stored as hashes.",
            ),
            (
                "Why we use this information",
                "Account and workspace information is used to provide the service you request and manage your access. Security information helps prevent abuse and protect accounts. Support information is used to respond to your requests. The relevant legal bases are performing the service agreement (Article 6(1)(b) GDPR), legitimate interests in secure and reliable operation (Article 6(1)(f)), and legal obligations where applicable (Article 6(1)(c)). You can manage purchasing-launch email preferences in AI usage.",
            ),
            (
                "Your business content",
                "Your workspace owner controls who can access business requests and knowledge. Upload only information you are authorized to share. Requests may contain other people's data; your business is responsible for its instructions and lawful use of that content. Keep passwords, API keys and payment-card details out of requests and documents. Service access is limited to authorized workspace members and operational access needed to maintain or support the service.",
            ),
            (
                "AI processing",
                "When you index a document, its text is sent to OpenAI to create embeddings. When you prepare a reply, OpenAI processes the request and relevant knowledge passages, instructions and templates. The service stores the result, sources and model-usage information. Responses requests use store:false; this does not mean that all provider-side records are immediately deleted. OpenAI's applicable service terms and data policies also govern its processing. An employee reviews AI proposals before using them; the service does not make binding decisions about people or send replies automatically.",
            ),
            (
                "Service providers",
                "The VPS is hosted by OVHcloud. OpenAI provides the language and embedding models. Google Gmail delivers account-verification and recovery emails. Cloudflare Turnstile checks registration requests for abuse. Business records and the Qdrant knowledge index are stored on the service's server. Information is shared with a provider only as needed for its role; the service does not read your Gmail inbox.",
            ),
            (
                "Processing locations",
                "Providers may process information outside the European Economic Area. Their service agreements and published privacy information describe processing locations and applicable international-transfer safeguards. Contact us for information about the arrangements relevant to your account before submitting content with specific location requirements.",
            ),
            (
                "Retention and deletion",
                "Account and business records are kept while needed to provide the workspace. You can export account and workspace data and submit an account-deletion request in Settings. Deletion requests are reviewed with any shared business records and applicable retention obligations; submitting a request does not immediately erase the workspace. Usage records support the one-time AI allowance and abuse prevention. Expired sessions and rate-limit records are cleaned up after approximately one day, expired recovery-token records after seven days, and completed email-job records after thirty days. Cleanup runs in the background; records can remain longer while a job is delayed.",
            ),
            (
                "Cookies and browser storage",
                "An essential, secure, HTTP-only session cookie keeps you signed in. Browser storage can remember interface preferences. Turnstile processes technical browser and network information for its security check. The application does not include advertising or third-party analytics trackers. Its separate interactive sample can store synthetic demo data in your browser.",
            ),
            (
                "Your choices and rights",
                f"Depending on applicable law, you may request access, correction, deletion, restriction or portability of your information, and object to processing based on legitimate interests. You can cancel a purchasing-launch notification in AI usage. Contact {contact} to exercise your rights or ask a privacy question. We may need to verify your identity. You may also complain to the competent data-protection supervisory authority, including the authority in your German federal state or place of residence.",
            ),
        ]
        links = [
            (
                "OVHcloud privacy",
                "https://www.ovhcloud.com/en/personal-data-protection/",
            ),
            ("OpenAI business data", "https://openai.com/enterprise-privacy/"),
            ("Google privacy", "https://policies.google.com/privacy"),
            ("Cloudflare privacy", "https://www.cloudflare.com/privacypolicy/"),
        ]
    else:
        title = "Service terms"
        allowance = f"${config.demo_ai_budget_usd:.2f}"
        sections = [
            ("Service operator", identity),
            (
                "What the service does",
                "SupportOps AI helps businesses organize incoming and internal requests, maintain their own knowledge and templates, prepare AI-assisted replies and record human review. It is intended for business workflows. Use the service only when you are authorized to act for the business and share the information you submit.",
            ),
            (
                "Accounts and early access",
                f"This release has a service-wide limit of {config.registration_limit} registered accounts, including existing accounts and new users joining by invitation. New registrations pause when all places are taken; existing users can continue to sign in. Workspace owners manage membership and access. Keep your password private and contact us promptly if you suspect unauthorized use.",
            ),
            (
                "Demo access and AI allowance",
                f"The current demo includes a one-time {allowance} AI allowance per user account, shared across that account's workspaces. Document embeddings, agent processing and model verification use this allowance. A run temporarily reserves capacity, then reconciles recorded usage. Costs use configured provider token rates; uncertain charges retain a reservation until reconciled. Paid AI operations pause when the allowance is exhausted. Manual workspace features remain available."
                if config.demo_release
                else "The plan and AI allowance shown in your workspace apply to model processing, document indexing and verification. Usage depends on the amount of text and processing required. Running work temporarily reserves capacity, then reconciles recorded usage.",
            ),
            (
                "Payments and launch emails",
                "Purchasing is not available in this demo release. The service does not collect payment-card details or charge a subscription. AI-allowance status and purchasing-launch notification preferences are recorded on your account. You can request or cancel a launch email in AI usage. Any future paid offer will show its pricing and terms before a purchase."
                if config.demo_release
                else "Review the price, renewal period and cancellation terms presented before checkout. Payment processing is handled by the configured payment provider. Contact support for billing questions.",
            ),
            (
                "Human review and external actions",
                "AI output can be incomplete or incorrect. Check the sources and edit the proposed reply before approving it. Approval saves the review decision; it does not send an email, issue a refund, cancel an order, approve an expense or change an external business record. A person remains responsible for decisions and actions taken using the result.",
            ),
            (
                "Your content and permitted use",
                "You retain responsibility for the requests, documents and templates your business supplies. Provide information you are entitled to use, keep rules current and respect other people's privacy and intellectual property. Do not use the service for unlawful content, unauthorized access, spam, malicious code or attempts to bypass account and AI limits. Access may be restricted to protect the service or investigate misuse.",
            ),
            (
                "Availability and changes",
                "This is a limited demo release. Features may change, and maintenance or provider interruptions can affect availability. There is no separate uptime or response-time commitment for the demo. Keep copies of information important to your business and use the export tools. Statutory rights and responsibilities remain unaffected.",
            ),
            (
                "Leaving the service and contacting us",
                f"You can export your account information and request account deletion in Settings. Workspace owners can also export shared business records. Deletion of shared content is reviewed with the business owner and any applicable obligations. Send service, access or privacy questions to {contact}. These terms and the Privacy notice describe the current service; material changes will be reflected in the published notices.",
            ),
        ]
        links = [("Privacy notice", "/privacy")]
    esc = html.escape
    content = "".join(
        f"<section><h2>{esc(heading)}</h2><p>{esc(text)}</p></section>"
        for heading, text in sections
    )
    references = " · ".join(
        f'<a href="{esc(url, quote=True)}">{esc(label)}</a>' for label, url in links
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex"><title>'
        + title
        + " · SupportOps AI</title>"
        '<link rel="stylesheet" href="/assets/style.css"></head><body><main class="policy-document">'
        '<a href="/">← SupportOps AI</a><h1>'
        + title
        + "</h1><p>Effective date: 22 October 2026</p>"
        + content
        + "<section><h2>Further information</h2><p>"
        + references
        + "</p></section>"
        "<footer><p>"
        + esc(operator)
        + " · "
        + esc(country)
        + ' · <a href="mailto:'
        + esc(contact, quote=True)
        + '">'
        + esc(contact)
        + "</a></p></footer>"
        "</main></body></html>"
    )
