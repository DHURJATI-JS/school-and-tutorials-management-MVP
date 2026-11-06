"""Shared constants / choice lists."""

WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

STAFF_ROLES: dict[str, str] = {
    "teacher": "Teacher",
    "principal": "Principal",
    "vice_principal": "Vice Principal",
    "coordinator": "Coordinator",
    "sme": "Subject Matter Expert (SME)",
    "counselor": "Counsellor",
    "accountant": "Accountant",
    "librarian": "Librarian",
    "lab_assistant": "Lab Assistant",
    "receptionist": "Receptionist",
    "cleaner": "Cleaner / Housekeeping",
    "security": "Security",
    "driver": "Driver",
    "other": "Other",
}
# Roles that teach / supervise: they can host live classes and see batch data.
TEACHING_ROLES = {"teacher", "principal", "vice_principal", "coordinator", "sme"}
SUPERVISOR_ROLES = {"principal", "vice_principal", "coordinator"}

ID_PROOF_TYPES = ["Aadhaar Card", "PAN Card", "Passport", "Voter ID", "Driving Licence", "School ID", "Birth Certificate", "Other"]
GENDERS = ["Female", "Male", "Other", "Prefer not to say"]
PARENT_RELATIONS = ["Mother", "Father", "Guardian"]
CLASS_LEVELS = [f"Class {i}" for i in range(1, 13)] + ["Undergraduate", "Competitive Exams", "Adult Learning"]
SUBJECT_SUGGESTIONS = ["Mathematics", "Science", "Physics", "Chemistry", "Biology", "English", "Hindi", "Social Studies",
                       "History", "Geography", "Computer Science", "Accountancy", "Economics", "General Knowledge"]

TASK_KINDS: dict[str, str] = {
    "assignment": "Assignment",
    "homework": "Homework",
    "test": "Test",
    "reading": "Reading",
    "project": "Project",
    "revision": "Revision",
}
DIFFICULTIES = ["easy", "medium", "hard", "mixed"]
QUESTION_KINDS = {"mcq": "Multiple choice", "true_false": "True / False", "short": "Short answer"}
PAYMENT_METHODS = ["Cash", "UPI", "Bank transfer", "Card", "Cheque", "Other"]
SEVERITIES = ["low", "medium", "high"]

REMOVAL_TEMPLATES: dict[str, dict[str, str]] = {
    "spamming": {
        "label": "Spamming",
        "reason": "Repeated spamming in class chats, live classes or communication channels despite reminders.",
    },
    "bad_behaviour": {
        "label": "Bad behaviour",
        "reason": "Disrespectful or inappropriate behaviour towards teachers, staff or fellow students.",
    },
    "academic_dishonesty": {
        "label": "Academic dishonesty",
        "reason": "Cheating, plagiarism or other academic dishonesty in tests or assignments.",
    },
    "fee_default": {
        "label": "Fee non-payment",
        "reason": "Fees remain unpaid beyond the permitted grace period despite multiple reminders.",
    },
    "attendance": {
        "label": "Prolonged absence",
        "reason": "Prolonged absence from classes without prior notice or valid reason.",
    },
    "policy_violation": {
        "label": "Policy violation",
        "reason": "Violation of the institute's code of conduct and policies.",
    },
    "custom": {"label": "Custom (write your own)", "reason": ""},
}

HOME_SECTION_KINDS: dict[str, dict] = {
    "hero": {"label": "Hero banner", "icon": "fa-star", "fields": []},
    "features": {"label": "Feature grid", "icon": "fa-table-cells-large", "fields": ["icon", "title", "text"]},
    "stats": {"label": "Statistics", "icon": "fa-chart-simple", "fields": ["value", "label"]},
    "steps": {"label": "How it works", "icon": "fa-shoe-prints", "fields": ["title", "text"]},
    "testimonials": {"label": "Testimonials", "icon": "fa-quote-left", "fields": ["quote", "name", "role"]},
    "faq": {"label": "FAQ", "icon": "fa-circle-question", "fields": ["q", "a"]},
    "gallery": {"label": "Gallery", "icon": "fa-images", "fields": ["src", "caption"]},
    "richtext": {"label": "Rich text", "icon": "fa-align-left", "fields": []},
    "cta": {"label": "Call to action", "icon": "fa-bullhorn", "fields": []},
    "contact": {"label": "Contact", "icon": "fa-address-card", "fields": []},
}
