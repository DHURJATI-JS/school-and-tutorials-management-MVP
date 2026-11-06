"""First-run content: branding, a feature-showcase home page and legal pages."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import HomeSection, Page, SiteSetting

FEATURES = [
    ("fa-video", "Live classes, built in",
     "Google-Meet-style video classes with screen sharing, chat, hand-raise, captions and automatic recording."),
    ("fa-wand-magic-sparkles", "AI class notes",
     "Every live class is transcribed and turned into neat, structured notes by your own local AI."),
    ("fa-robot", "24×7 AI doubt solver",
     "Students ask questions about any class and get instant answers grounded in that class's notes."),
    ("fa-file-circle-question", "AI test generator",
     "Class- and subject-aware tests in seconds. Review, edit and publish them to a batch."),
    ("fa-calendar-check", "Holiday-aware calendar",
     "Holidays block assignments and deadlines automatically; vacations allow holiday homework only."),
    ("fa-list-check", "Task-based scheduling",
     "Assignments, homework and tests land on every student's calendar with clear deadlines."),
    ("fa-user-check", "Automatic attendance",
     "Marked when a student attends the live class or watches at least half of the recorded lesson."),
    ("fa-chart-line", "Performance insights",
     "AI watches scores, attendance and submissions and alerts admins, teachers and parents early."),
    ("fa-id-card", "Virtual ID cards & OCR login",
     "Every member gets a digital ID card. Scan it to sign in with a one-time code on email and phone."),
    ("fa-indian-rupee-sign", "Fees & payroll",
     "Track fee payments, print receipts and share salary slips with staff securely."),
    ("fa-people-roof", "PTMs & communication",
     "Formal PTM invitations with RSVP, broadcast notices and one-click WhatsApp contact."),
    ("fa-hand-holding-heart", "Inclusive by design",
     "Health records and special-needs notes make every AI message gentle, patient and encouraging."),
    ("fa-shield-halved", "Safe & secure",
     "Abusive-language filter with auto-bans, IP blocking, CSRF + CSP protection and bcrypt passwords."),
    ("fa-microchip", "Private, local AI",
     "Runs on your own Gemma model via an OpenAI-compatible gateway — your data stays on your network."),
]


def default_sections() -> list[dict]:
    return [
        {"kind": "hero", "title": "Smarter classrooms for small schools & tuition centres",
         "subtitle": "Live classes, AI notes, AI tests, holiday-aware scheduling, attendance, fees and parent "
                     "communication — in one fast, private platform powered by your own local AI.",
         "data": {"badge": "✨ AI-powered • runs on your own network", "primary_label": "Explore features",
                  "primary_url": "#features", "secondary_label": "Sign in", "secondary_url": "/login"}},
        {"kind": "stats", "title": "", "subtitle": "",
         "data": {"items": [{"value": "12", "label": "batches a day"}, {"value": "12 h", "label": "of daily scheduling"},
                            {"value": "100%", "label": "local, private AI"}, {"value": "2-step", "label": "OTP sign-in"}]}},
        {"kind": "features", "title": "Everything your institute needs",
         "subtitle": "Built for teachers, loved by students, trusted by parents.",
         "data": {"items": [{"icon": i, "title": t, "text": x} for i, t, x in FEATURES]}},
        {"kind": "steps", "title": "How it works", "subtitle": "Up and running in an afternoon.",
         "data": {"items": [
             {"title": "Create batches", "text": "Add up to 12 batches (1 hour minimum each, 12 hours a day in total)."},
             {"title": "Add students & staff", "text": "They instantly receive a virtual ID card and confidential login."},
             {"title": "Teach & assign", "text": "Go live or upload a video, generate tests with AI and assign tasks."},
             {"title": "Let AI assist", "text": "Notes, doubt-solving, attendance and progress alerts happen automatically."}]}},
        {"kind": "faq", "title": "Frequently asked questions", "subtitle": "",
         "data": {"items": [
             {"q": "Does the AI send our data to the cloud?",
              "a": "No. It talks to your own OpenAI-compatible gateway (for example a local Gemma model)."},
             {"q": "Can we give homework during the summer vacation?",
              "a": "Yes. Mark a vacation as allowing holiday homework; normal assignments stay blocked."},
             {"q": "How is attendance calculated?",
              "a": "Students are marked present when they attend the live class or watch at least 50% of the video."},
             {"q": "What happens if someone is abusive in chat?",
              "a": "Messages are filtered automatically; repeat offenders are blocked and the admin is informed."}]}},
        {"kind": "cta", "title": "Ready to modernise your institute?",
         "subtitle": "Talk to us on WhatsApp or email — we usually reply within a day.",
         "data": {"primary_label": "Chat on WhatsApp", "primary_url": "whatsapp",
                  "secondary_label": "Send an email", "secondary_url": "email"}},
        {"kind": "contact", "title": "Get in touch", "subtitle": "We'd love to hear from you.",
         "data": {"show_form": True}},
    ]


def terms_html() -> str:
    today = dt.date.today().strftime("%d %B %Y")
    return f"""
<p><strong>Last updated:</strong> {today}</p>
<div class="cn-callout"><h3>Important disclaimer — student-grade project</h3>
<p><strong>This software is a student-grade project built for learning and demonstration purposes only.</strong>
It is provided “AS IS” and “AS AVAILABLE”, without warranties of any kind, express or implied, including but not
limited to merchantability, fitness for a particular purpose, accuracy, security or uninterrupted availability.</p>
<p><strong>The author / developer is NOT responsible or liable</strong> for any loss or damage of any kind —
including data loss, data breaches, privacy issues, financial loss (including fee or salary records), missed or
interrupted classes, incorrect grades or attendance, AI-generated mistakes, account bans, or any other consequence —
arising from the use of, or inability to use, this application. You use it entirely at your own risk. Have it
reviewed by qualified professionals before using it with real students, staff, children's data or money.</p></div>
<h3>1. Acceptance</h3><p>By using this platform you agree to these terms. If you do not agree, do not use it.</p>
<h3>2. AI-generated content</h3><p>Tests, notes, answers, grades and alerts may be produced by an AI model and can be
inaccurate or incomplete. Teachers and administrators must review AI output before relying on it.</p>
<h3>3. Accounts &amp; security</h3><p>Login credentials and ID cards are personal and confidential. Never share your
password or one-time codes. Administrators may suspend or remove accounts and must state a reason.</p>
<h3>4. Acceptable use</h3><p>Abusive, hateful or inappropriate language is filtered automatically. Repeated
violations lead to an automatic block of the network address (IP ban), and both the administrator and the person
are informed of the reason.</p>
<h3>5. Live classes &amp; recordings</h3><p>Live classes may be recorded and transcribed to create class notes and
answer students' doubts. Participants are shown a recording indicator.</p>
<h3>6. Children's data &amp; consent</h3><p>The institution operating this platform is responsible for obtaining
consent from parents/guardians and for complying with applicable law (for example India's Digital Personal Data
Protection Act, 2023).</p>
<h3>7. Payments</h3><p>Fee and salary features are record-keeping tools only; no payments are processed.</p>
<h3>8. Changes</h3><p>These terms may change at any time. Continued use means you accept the updated terms.</p>
<h3>9. Contact</h3><p>Email <a href="mailto:{settings.CONTACT_EMAIL}">{settings.CONTACT_EMAIL}</a> or WhatsApp
{settings.CONTACT_WHATSAPP}.</p>"""


def privacy_html() -> str:
    return f"""
<p>This platform stores the information an institute needs to run classes: names, contact details, date of birth,
photos, ID proofs, parent details, health notes (only if provided), attendance, grades, fee records and class
recordings.</p>
<h3>How data is used</h3><p>Only to run the institute: classes, attendance, assessment, communication and safety.
AI features run on the institute's own AI server; data is not sold or shared with advertisers.</p>
<h3>Security</h3><p>Passwords are hashed with bcrypt, forms are CSRF-protected, pages use a strict Content Security
Policy and abusive or suspicious traffic can be blocked.</p>
<h3>Your rights</h3><p>Contact the institute at <a href="mailto:{settings.CONTACT_EMAIL}">{settings.CONTACT_EMAIL}</a>
to access, correct or delete your data.</p>
<p><em>This is a student-grade project — see the Terms &amp; Conditions disclaimer.</em></p>"""


def about_html() -> str:
    return """
<p>We are a small institute that believes every learner deserves patient, personal attention. Our platform combines
live teaching with private, on-premise AI so teachers spend less time on paperwork and more time teaching.</p>
<ul><li>Small batches with dedicated teachers</li><li>AI-assisted notes, tests and doubt solving</li>
<li>Transparent attendance, results and fee records for parents</li></ul>"""


async def run(db: AsyncSession) -> None:
    if await db.get(SiteSetting, "branding") is None:
        db.add(SiteSetting(key="branding", value={"site_name": settings.APP_NAME, "tagline": settings.APP_TAGLINE}))
    if not await db.scalar(select(func.count()).select_from(HomeSection)):
        for pos, section in enumerate(default_sections()):
            db.add(HomeSection(position=pos, is_visible=True, **section))
    if not await db.scalar(select(func.count()).select_from(Page)):
        db.add_all([
            Page(slug="terms", title="Terms & Conditions", body=terms_html()),
            Page(slug="privacy", title="Privacy Policy", body=privacy_html()),
            Page(slug="about", title="About us", body=about_html()),
        ])
    await db.commit()
