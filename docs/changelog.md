"""

============================================================

پروژه «نظم داد — Nazm Dad»

خلاصه کامل وضعیت پروژه برای ادامه توسعه v0.2



مخزن:

&#x20;   13640226/nazm-dad



شاخه پایدار:

&#x20;   main



شاخه توسعه:

&#x20;   develop/v0.2



نسخه پایدار فعلی:

&#x20;   v0.1-site



نسخه در حال توسعه:

&#x20;   v0.2



این فایل:

\- وضعیت پروژه را نگهداری می‌کند.

\- وضعیت اسناد را نمایش می‌دهد.

\- changelog رسمی ۰.۴ → ۰.۵ را ثبت می‌کند.

\- دستورات امن Git برای ادامه کار را نمایش می‌دهد.

\- هیچ فایل پروژه را خودکار تغییر نمی‌دهد.

\- هیچ commit یا push خودکاری انجام نمی‌دهد.



نکته:

دسته‌های T / A / R / RC در changelog می‌توانند هم‌پوشانی داشته باشند؛

بنابراین جمع عددی آنها برابر تعداد «تغییرات واقعی یکتا» نیست.

طبق changelog رسمی:

&#x20;   تغییرات واقعی = 21

&#x20;   No-op = 2

============================================================

"""



from dataclasses import dataclass

from typing import List, Dict, Optional

from enum import Enum





\# ============================================================

\# Enumها

\# ============================================================



class Branch(str, Enum):

&#x20;   MAIN = "main"

&#x20;   DEVELOP = "develop/v0.2"





class RepoVisibility(str, Enum):

&#x20;   PUBLIC = "Public"

&#x20;   PRIVATE = "Private"





class DocumentStatus(str, Enum):

&#x20;   COMPLETE = "کامل و authoritative"

&#x20;   PLACEHOLDER = "Placeholder"

&#x20;   PENDING = "در انتظار تکمیل"

&#x20;   VERIFY = "آماده، منتظر تأیید Git"





class ChangeType(str, Enum):

&#x20;   T = "الحاق تأسیسی (T)"

&#x20;   A = "جایگزینی/اصلاح تأسیسی (A)"

&#x20;   R = "تنقیح غیرتأسیسی (R)"

&#x20;   RC = "اصلاحات فرایندی (RC)"





\# ============================================================

\# Data Classes

\# ============================================================



@dataclass

class RepoInfo:

&#x20;   """اطلاعات مخزن GitHub"""



&#x20;   name: str = "nazm-dad"

&#x20;   owner: str = "13640226"



&#x20;   visibility: RepoVisibility = RepoVisibility.PUBLIC



&#x20;   url: str = "https://github.com/13640226/nazm-dad"



&#x20;   pages\_url: str = (

&#x20;       "https://13640226.github.io/nazm-dad/"

&#x20;   )



&#x20;   english\_url: str = (

&#x20;       "https://13640226.github.io/nazm-dad/en/"

&#x20;   )



&#x20;   default\_branch: Branch = Branch.MAIN

&#x20;   development\_branch: Branch = Branch.DEVELOP





@dataclass

class LocalInfo:

&#x20;   """اطلاعات محیط محلی"""



&#x20;   path: str = r"C:\\Users\\hamid\\Desktop\\nazm-dad"

&#x20;   shell: str = "PowerShell"

&#x20;   operating\_system: str = "Windows"

&#x20;   git\_installed: bool = True





@dataclass

class VersionInfo:

&#x20;   """اطلاعات نسخه‌بندی"""



&#x20;   stable\_version: str = "v0.1-site"

&#x20;   development\_version: str = "v0.2"



&#x20;   current\_branch: Branch = Branch.DEVELOP



&#x20;   stable\_tag: str = "v0.1-site"

&#x20;   stable\_release\_published: bool = True



&#x20;   main\_must\_remain\_stable: bool = True





@dataclass

class DocumentInfo:

&#x20;   """اطلاعات یک سند"""



&#x20;   name: str

&#x20;   status: DocumentStatus



&#x20;   articles\_count: Optional\[int] = None



&#x20;   description: str = ""





@dataclass

class ChangeLogEntry:

&#x20;   """یک دسته از تغییرات changelog"""



&#x20;   change\_type: ChangeType

&#x20;   count: int

&#x20;   items: List\[str]

&#x20;   description: str = ""





@dataclass

class SiteInfo:

&#x20;   """وضعیت سایت عمومی"""



&#x20;   persian\_working: bool = True

&#x20;   english\_working: bool = True



&#x20;   custom\_404: bool = True

&#x20;   robots\_txt: bool = True

&#x20;   sitemap\_xml: bool = True



&#x20;   github\_pages\_deployed: bool = True





\# ============================================================

\# کلاس اصلی پروژه

\# ============================================================



class NazmDadProject:

&#x20;   """

&#x20;   مدل وضعیت پروژه نظم داد.



&#x20;   این کلاس هیچ تغییری در فایل‌ها یا Git ایجاد نمی‌کند.

&#x20;   صرفاً اطلاعات پروژه، changelog و مراحل بعدی را نمایش می‌دهد.

&#x20;   """



&#x20;   def \_\_init\_\_(self):

&#x20;       self.repo = RepoInfo()

&#x20;       self.local = LocalInfo()

&#x20;       self.version = VersionInfo()

&#x20;       self.site = SiteInfo()



&#x20;       self.\_init\_documents()

&#x20;       self.\_init\_changelog()



&#x20;   # --------------------------------------------------------

&#x20;   # اسناد

&#x20;   # --------------------------------------------------------



&#x20;   def \_init\_documents(self):

&#x20;       """

&#x20;       تعریف وضعیت اسناد.



&#x20;       توجه:

&#x20;       وضعیت COMPLETE در اینجا به معنی آماده بودن محتوای سند است.

&#x20;       هنوز برای ثبت نهایی در Git باید git status بررسی شود.

&#x20;       """



&#x20;       self.documents: Dict\[str, DocumentInfo] = {



&#x20;           "0.4.md": DocumentInfo(

&#x20;               name="0.4.md",

&#x20;               status=DocumentStatus.COMPLETE,

&#x20;               articles\_count=61,

&#x20;               description=(

&#x20;                   "نسخه ۰.۴ نهایی و قفل‌شده؛ "

&#x20;                   "متن authoritative شامل ۶۱ ماده."

&#x20;               )

&#x20;           ),



&#x20;           "0.5.md": DocumentInfo(

&#x20;               name="0.5.md",

&#x20;               status=DocumentStatus.VERIFY,

&#x20;               articles\_count=73,

&#x20;               description=(

&#x20;                   "متن کامل نسخه ۰.۵ در فایل جایگزین شده؛ "

&#x20;                   "منتظر تأیید git status پیش از commit."

&#x20;               )

&#x20;           ),



&#x20;           "changelog.md": DocumentInfo(

&#x20;               name="changelog.md",

&#x20;               status=DocumentStatus.VERIFY,

&#x20;               description=(

&#x20;                   "Changelog رسمی ۰.۴ → ۰.۵؛ "

&#x20;                   "باید ذخیره و سپس با git status تأیید شود."

&#x20;               )

&#x20;           ),



&#x20;           "rules.md": DocumentInfo(

&#x20;               name="rules.md",

&#x20;               status=DocumentStatus.COMPLETE,

&#x20;               description=(

&#x20;                   "دفتر قواعد تفسیری پروژه؛ "

&#x20;                   "در این مرحله نباید تغییر کند."

&#x20;               )

&#x20;           ),



&#x20;           "decisions.md": DocumentInfo(

&#x20;               name="decisions.md",

&#x20;               status=DocumentStatus.COMPLETE,

&#x20;               description=(

&#x20;                   "دفتر تصمیمات تأسیسی؛ "

&#x20;                   "در این مرحله نباید تغییر کند."

&#x20;               )

&#x20;           ),

&#x20;       }



&#x20;   # --------------------------------------------------------

&#x20;   # Changelog رسمی

&#x20;   # --------------------------------------------------------



&#x20;   def \_init\_changelog(self):

&#x20;       """

&#x20;       Changelog رسمی نسخه ۰.۴ → ۰.۵.



&#x20;       نکته بسیار مهم:

&#x20;       دسته‌های زیر کاملاً مستقل از هم نیستند و بعضی تغییرات

&#x20;       در بیش از یک دسته ثبت شده‌اند.



&#x20;       بنابراین:

&#x20;           15 + 3 + 3 + 4

&#x20;       نباید به‌عنوان تعداد تغییرات یکتای واقعی محاسبه شود.



&#x20;       تعداد رسمی تغییرات واقعی یکتا:

&#x20;           21



&#x20;       No-op:

&#x20;           2

&#x20;       """



&#x20;       self.changelog: List\[ChangeLogEntry] = \[



&#x20;           ChangeLogEntry(

&#x20;               change\_type=ChangeType.T,

&#x20;               count=15,

&#x20;               items=\[

&#x20;                   "ماده ۲۳-۱",

&#x20;                   "بند ۲۸(۶)",

&#x20;                   "ماده ۳۲-۱",

&#x20;                   "ماده ۳۲-۲",

&#x20;                   "ماده ۳۲-۳",

&#x20;                   "ماده ۳۷-۱",

&#x20;                   "ماده ۴۳-۱",

&#x20;                   "ماده ۴۶-۱",

&#x20;                   "ماده ۴۸-۱",

&#x20;                   "ماده ۵۲-۱",

&#x20;                   "ماده ۵۲-۲",

&#x20;                   "ماده ۵۴-۱",

&#x20;                   "ماده ۵۴-۲",

&#x20;                   "اصلاح ۲۳-۱(۳)(ب)",

&#x20;                   "الحاق پایانی ۲۳-۱(۶)",

&#x20;               ],

&#x20;               description=(

&#x20;                   "الحاقات و تغییرات تأسیسی ثبت‌شده "

&#x20;                   "در فرایند توسعه ۰.۵."

&#x20;               )

&#x20;           ),



&#x20;           ChangeLogEntry(

&#x20;               change\_type=ChangeType.A,

&#x20;               count=3,

&#x20;               items=\[

&#x20;                   "ماده ۴۶: جایگزینی بند ۴ قدیمی با بندهای ۴ تا ۱۱",

&#x20;                   (

&#x20;                       "مواد ۵۲، ۵۳ و ۵۴: "

&#x20;                       "هماهنگ‌سازی بخش عزل با ماده ۵۴-۱"

&#x20;                   ),

&#x20;                   "ماده ۲۳-۱(۳)(ب): اصلاح متن آزمون ضرورت",

&#x20;               ],

&#x20;               description=(

&#x20;                   "جایگزینی یا اصلاح محتوای تأسیسی موجود."

&#x20;               )

&#x20;           ),



&#x20;           ChangeLogEntry(

&#x20;               change\_type=ChangeType.R,

&#x20;               count=3,

&#x20;               items=\[

&#x20;                   (

&#x20;                       "ماده ۲۴: افزودن نصاب دو سوم "

&#x20;                       "کل اعضای مجلس استان‌ها"

&#x20;                   ),

&#x20;                   (

&#x20;                       "ماده ۴۳: اصلاح نحوی به "

&#x20;                       "«هیئتی مستقل، متشکل از قضات عالی‌رتبه»"

&#x20;                   ),

&#x20;                   (

&#x20;                       "ماده ۵۹: حذف بند ۳ به دلیل "

&#x20;                       "هم‌پوشانی با ماده ۶۱"

&#x20;                   ),

&#x20;               ],

&#x20;               description="تنقیحات غیرتأسیسی."

&#x20;           ),



&#x20;           ChangeLogEntry(

&#x20;               change\_type=ChangeType.RC,

&#x20;               count=4,

&#x20;               items=\[

&#x20;                   (

&#x20;                       "RC1-C: افزودن بند ۷ به ماده ۲۸ "

&#x20;                       "(دوره ریاست‌جمهوری چهار سال)"

&#x20;                   ),

&#x20;                   (

&#x20;                       "RC1-A: هماهنگ‌سازی عزل نهادهای مستقل "

&#x20;                       "با ماده ۵۴-۱"

&#x20;                   ),

&#x20;                   (

&#x20;                       "RC1-B: فهرست بسته صلاحیت‌های مشترک"

&#x20;                   ),

&#x20;                   (

&#x20;                       "RC2 / RC4: اصلاح ۲۳-۱(۳)(ب)، "

&#x20;                       "الحاق ماده ۵۴-۲ و ممنوعیت "

&#x20;                       "فعال‌سازی متوالی ۲۳-۱(۶)"

&#x20;                   ),

&#x20;               ],

&#x20;               description="اصلاحات فرایندی ثبت‌شده."

&#x20;           ),

&#x20;       ]



&#x20;   # --------------------------------------------------------

&#x20;   # مقادیر رسمی

&#x20;   # --------------------------------------------------------



&#x20;   @property

&#x20;   def total\_changes(self) -> int:

&#x20;       """

&#x20;       تعداد تغییرات واقعی یکتا طبق changelog رسمی.



&#x20;       این مقدار از جمع دسته‌های T/A/R/RC محاسبه نمی‌شود،

&#x20;       زیرا دسته‌ها دارای هم‌پوشانی هستند.

&#x20;       """

&#x20;       return 21



&#x20;   @property

&#x20;   def noop\_changes(self) -> int:

&#x20;       """تعداد موارد ثبت‌شده بدون تغییر ماهوی."""

&#x20;       return 2



&#x20;   @property

&#x20;   def total\_articles\_v04(self) -> int:

&#x20;       """تعداد مواد نسخه ۰.۴"""

&#x20;       return 61



&#x20;   @property

&#x20;   def total\_articles\_v05(self) -> int:

&#x20;       """تعداد مواد اعلام‌شده نسخه ۰.۵"""

&#x20;       return 73



&#x20;   @property

&#x20;   def added\_independent\_articles(self) -> int:

&#x20;       """افزایش تعداد مواد مستقل"""

&#x20;       return (

&#x20;           self.total\_articles\_v05

&#x20;           - self.total\_articles\_v04

&#x20;       )



&#x20;   # --------------------------------------------------------

&#x20;   # دریافت اطلاعات

&#x20;   # --------------------------------------------------------



&#x20;   def get\_document\_status(

&#x20;       self,

&#x20;       name: str

&#x20;   ) -> Optional\[DocumentInfo]:

&#x20;       """دریافت وضعیت یک سند"""

&#x20;       return self.documents.get(name)



&#x20;   def get\_change\_summary(self) -> Dict\[str, int]:

&#x20;       """خلاصه آماری changelog"""



&#x20;       return {

&#x20;           entry.change\_type.value: entry.count

&#x20;           for entry in self.changelog

&#x20;       }



&#x20;   # --------------------------------------------------------

&#x20;   # نمایش وضعیت

&#x20;   # --------------------------------------------------------



&#x20;   @staticmethod

&#x20;   def \_yes\_no(value: bool) -> str:

&#x20;       return "✅" if value else "❌"



&#x20;   def print\_summary(self):

&#x20;       """چاپ خلاصه کامل پروژه"""



&#x20;       width = 78



&#x20;       print("=" \* width)

&#x20;       print(

&#x20;           "پروژه نظم داد — Nazm Dad".center(width)

&#x20;       )

&#x20;       print("=" \* width)



&#x20;       # مخزن

&#x20;       print("\\n📦 مخزن GitHub")

&#x20;       print(

&#x20;           f"  Repository: "

&#x20;           f"{self.repo.owner}/{self.repo.name}"

&#x20;       )

&#x20;       print(

&#x20;           f"  Visibility: "

&#x20;           f"{self.repo.visibility.value}"

&#x20;       )

&#x20;       print(

&#x20;           f"  URL: {self.repo.url}"

&#x20;       )

&#x20;       print(

&#x20;           f"  Stable branch: "

&#x20;           f"{self.repo.default\_branch.value}"

&#x20;       )

&#x20;       print(

&#x20;           f"  Development branch: "

&#x20;           f"{self.repo.development\_branch.value}"

&#x20;       )



&#x20;       # محیط

&#x20;       print("\\n💻 محیط محلی")

&#x20;       print(

&#x20;           f"  Path: {self.local.path}"

&#x20;       )

&#x20;       print(

&#x20;           f"  OS: {self.local.operating\_system}"

&#x20;       )

&#x20;       print(

&#x20;           f"  Shell: {self.local.shell}"

&#x20;       )

&#x20;       print(

&#x20;           f"  Git installed: "

&#x20;           f"{self.\_yes\_no(self.local.git\_installed)}"

&#x20;       )



&#x20;       # نسخه

&#x20;       print("\\n🏷️ نسخه‌بندی")

&#x20;       print(

&#x20;           f"  Stable release: "

&#x20;           f"{self.version.stable\_version}"

&#x20;       )

&#x20;       print(

&#x20;           f"  Development version: "

&#x20;           f"{self.version.development\_version}"

&#x20;       )

&#x20;       print(

&#x20;           f"  Current development branch: "

&#x20;           f"{self.version.current\_branch.value}"

&#x20;       )

&#x20;       print(

&#x20;           f"  Stable tag: "

&#x20;           f"{self.version.stable\_tag}"

&#x20;       )

&#x20;       print(

&#x20;           "  Release published: "

&#x20;           f"{self.\_yes\_no(

&#x20;               self.version.stable\_release\_published

&#x20;           )}"

&#x20;       )

&#x20;       print(

&#x20;           "  main must remain stable: "

&#x20;           f"{self.\_yes\_no(

&#x20;               self.version.main\_must\_remain\_stable

&#x20;           )}"

&#x20;       )



&#x20;       # اسناد

&#x20;       print("\\n📄 وضعیت اسناد")



&#x20;       for name, doc in self.documents.items():



&#x20;           if doc.status == DocumentStatus.COMPLETE:

&#x20;               icon = "✅"



&#x20;           elif doc.status == DocumentStatus.VERIFY:

&#x20;               icon = "🔎"



&#x20;           elif doc.status == DocumentStatus.PLACEHOLDER:

&#x20;               icon = "⏳"



&#x20;           else:

&#x20;               icon = "⏳"



&#x20;           article\_text = ""



&#x20;           if doc.articles\_count is not None:

&#x20;               article\_text = (

&#x20;                   f" — {doc.articles\_count} ماده"

&#x20;               )



&#x20;           print(

&#x20;               f"  {icon} {name}{article\_text}"

&#x20;           )



&#x20;           print(

&#x20;               f"      وضعیت: {doc.status.value}"

&#x20;           )



&#x20;           if doc.description:

&#x20;               print(

&#x20;                   f"      {doc.description}"

&#x20;               )



&#x20;       # changelog

&#x20;       print("\\n📊 Changelog رسمی ۰.۴ → ۰.۵")



&#x20;       for entry in self.changelog:



&#x20;           print(

&#x20;               f"\\n  {entry.change\_type.value}: "

&#x20;               f"{entry.count}"

&#x20;           )



&#x20;           if entry.description:

&#x20;               print(

&#x20;                   f"      {entry.description}"

&#x20;               )



&#x20;           for item in entry.items:

&#x20;               print(

&#x20;                   f"      • {item}"

&#x20;               )



&#x20;       # جمع‌بندی رسمی

&#x20;       print("\\n📈 جمع‌بندی رسمی")



&#x20;       print(

&#x20;           "  تغییرات واقعی یکتا: "

&#x20;           f"{self.total\_changes}"

&#x20;       )



&#x20;       print(

&#x20;           f"  No-op: {self.noop\_changes}"

&#x20;       )



&#x20;       print(

&#x20;           f"  مواد نسخه ۰.۴: "

&#x20;           f"{self.total\_articles\_v04}"

&#x20;       )



&#x20;       print(

&#x20;           f"  مواد نسخه ۰.۵: "

&#x20;           f"{self.total\_articles\_v05}"

&#x20;       )



&#x20;       print(

&#x20;           "  افزایش مواد مستقل: "

&#x20;           f"{self.added\_independent\_articles}"

&#x20;       )



&#x20;       print(

&#x20;           "\\n  توجه: جمع T/A/R/RC برابر تعداد "

&#x20;           "تغییرات یکتا نیست، زیرا دسته‌ها "

&#x20;           "هم‌پوشانی دارند."

&#x20;       )



&#x20;       # سایت

&#x20;       print("\\n🌐 وضعیت سایت")



&#x20;       print(

&#x20;           "  Persian site: "

&#x20;           f"{self.\_yes\_no(

&#x20;               self.site.persian\_working

&#x20;           )}"

&#x20;       )



&#x20;       print(

&#x20;           "  English site: "

&#x20;           f"{self.\_yes\_no(

&#x20;               self.site.english\_working

&#x20;           )}"

&#x20;       )



&#x20;       print(

&#x20;           "  Custom 404: "

&#x20;           f"{self.\_yes\_no(

&#x20;               self.site.custom\_404

&#x20;           )}"

&#x20;       )



&#x20;       print(

&#x20;           "  robots.txt: "

&#x20;           f"{self.\_yes\_no(

&#x20;               self.site.robots\_txt

&#x20;           )}"

&#x20;       )



&#x20;       print(

&#x20;           "  sitemap.xml: "

&#x20;           f"{self.\_yes\_no(

&#x20;               self.site.sitemap\_xml

&#x20;           )}"

&#x20;       )



&#x20;       print(

&#x20;           "  GitHub Pages deployed: "

&#x20;           f"{self.\_yes\_no(

&#x20;               self.site.github\_pages\_deployed

&#x20;           )}"

&#x20;       )



&#x20;       print(

&#x20;           f"  Persian URL: "

&#x20;           f"{self.repo.pages\_url}"

&#x20;       )



&#x20;       print(

&#x20;           f"  English URL: "

&#x20;           f"{self.repo.english\_url}"

&#x20;       )



&#x20;       # وضعیت فعلی

&#x20;       print("\\n" + "-" \* width)



&#x20;       print(

&#x20;           "وضعیت فعلی: "

&#x20;           "docs/0.5.md آماده است؛ "

&#x20;           "docs/changelog.md باید ذخیره و "

&#x20;           "git status تأیید شود."

&#x20;       )



&#x20;       print(

&#x20;           "فعلاً commit یا push انجام نشود."

&#x20;       )



&#x20;       print("-" \* width)



&#x20;   # --------------------------------------------------------

&#x20;   # مراحل بعدی

&#x20;   # --------------------------------------------------------



&#x20;   def get\_immediate\_next\_steps(self) -> List\[str]:

&#x20;       """

&#x20;       فقط مراحل فوری.



&#x20;       فعلاً نباید commit یا push انجام شود

&#x20;       تا git status بررسی شود.

&#x20;       """



&#x20;       return \[

&#x20;           (

&#x20;               "docs/changelog.md را با متن رسمی "

&#x20;               "ذخیره کنید."

&#x20;           ),

&#x20;           (

&#x20;               "در PowerShell دستور git status "

&#x20;               "را اجرا کنید."

&#x20;           ),

&#x20;           (

&#x20;               "بررسی کنید فقط docs/0.5.md و "

&#x20;               "docs/changelog.md تغییر کرده باشند."

&#x20;           ),

&#x20;           (

&#x20;               "تا قبل از تأیید وضعیت، "

&#x20;               "git add / commit / push اجرا نکنید."

&#x20;           ),

&#x20;       ]



&#x20;   def get\_post\_verification\_git\_commands(

&#x20;       self

&#x20;   ) -> List\[str]:

&#x20;       """

&#x20;       دستوراتی که فقط پس از تأیید git status

&#x20;       باید اجرا شوند.

&#x20;       """



&#x20;       return \[

&#x20;           (

&#x20;               "git add "

&#x20;               "docs/0.5.md "

&#x20;               "docs/changelog.md"

&#x20;           ),

&#x20;           (

&#x20;               'git commit -m '

&#x20;               '"v0.2: replace docs/0.5.md '

&#x20;               'with authoritative 73-article text '

&#x20;               'and update changelog"'

&#x20;           ),

&#x20;           (

&#x20;               "git push origin develop/v0.2"

&#x20;           ),

&#x20;           (

&#x20;               "git status"

&#x20;           ),

&#x20;       ]



&#x20;   def get\_safety\_rules(self) -> List\[str]:

&#x20;       """قواعد ایمنی Git پروژه"""



&#x20;       return \[

&#x20;           "توسعه v0.2 فقط روی develop/v0.2 انجام شود.",

&#x20;           "main نسخه پایدار v0.1-site باقی بماند.",

&#x20;           "Tag v0.1-site حذف یا بازنویسی نشود.",

&#x20;           "Release v0.1-site دست‌نخورده بماند.",

&#x20;           "از git reset --hard بدون ضرورت استفاده نشود.",

&#x20;           "از force push استفاده نشود.",

&#x20;           "Branch یا Tag بدون ضرورت حذف نشود.",

&#x20;           "History بدون ضرورت بازنویسی نشود.",

&#x20;           (

&#x20;               "قبل از هر commit یا push مهم، "

&#x20;               "git status بررسی شود."

&#x20;           ),

&#x20;       ]



&#x20;   # --------------------------------------------------------

&#x20;   # چاپ مراحل بعدی

&#x20;   # --------------------------------------------------------



&#x20;   def print\_next\_steps(self):



&#x20;       print("\\n📋 گام‌های فوری")



&#x20;       for step in self.get\_immediate\_next\_steps():

&#x20;           print(f"  • {step}")



&#x20;       print("\\n🔐 قواعد ایمنی")



&#x20;       for rule in self.get\_safety\_rules():

&#x20;           print(f"  • {rule}")



&#x20;       print(

&#x20;           "\\n⚠️ دستورات زیر فقط بعد از "

&#x20;           "تأیید git status اجرا شوند:"

&#x20;       )



&#x20;       for command in (

&#x20;           self.get\_post\_verification\_git\_commands()

&#x20;       ):

&#x20;           print(f"  > {command}")





\# ============================================================

\# تابع اصلی

\# ============================================================



def main():



&#x20;   project = NazmDadProject()



&#x20;   project.print\_summary()



&#x20;   project.print\_next\_steps()



&#x20;   print("\\n" + "=" \* 78)



&#x20;   print(

&#x20;       "✅ پروژه آماده بررسی git status است"

&#x20;       .center(78)

&#x20;   )



&#x20;   print(

&#x20;       "❌ هنوز commit یا push انجام نشود"

&#x20;       .center(78)

&#x20;   )



&#x20;   print("=" \* 78)





\# ============================================================

\# اجرای برنامه

\# ============================================================



if \_\_name\_\_ == "\_\_main\_\_":

&#x20;   main()

