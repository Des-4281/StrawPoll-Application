# What Problem Does StrawPoll Solve?

---

## The Short Version

Most people have no idea how their senators actually vote. They know what politicians *say* — but not what they *do*. StrawPoll makes that voting record easy to find, easy to understand, and eventually, easy to compare against what people in that state actually want.

---

## The Longer Version

### Problem 1: Vote records are public, but practically invisible

Every Senate vote is a matter of public record. Senate.gov publishes them all. But finding out how your senator voted on a specific issue requires:
- Knowing which website to look at
- Knowing the bill number (e.g. S.1234 — meaningless on its own)
- Navigating government XML files designed for software, not people
- Doing this for every vote, every issue, every senator

Nobody does this. It's too hard. Politicians know that, and they count on it.

**What StrawPoll does:** Pulls every Senate vote into a searchable database. Ask a plain English question, get a plain English answer.

---

### Problem 2: Even if you find a vote, you can't understand it

"Senator X voted Nay on S.1234" tells you nothing. What is S.1234? What does a Nay vote mean in practice? Did it pass? What does it actually do to people?

Bill text is hundreds of pages of legal language written by lawyers for lawyers. News coverage only summarizes the headline purpose. The details — who benefits, who pays, what it changes in existing law — require deep reading that almost no one has time for.

**What StrawPoll does:** Fetches the full text of each bill, passes it through Claude, and stores a plain-English structured summary covering:
- What the bill actually does
- Who it helps and who it hurts
- How much it costs
- What legal authority it uses

---

### Problem 3: The fine print is where the real deals are made

The stated purpose of a bill and what the bill actually does are often two different things. Legislation regularly contains:

- **Earmarks** — funding directed to a specific state, city, or congressional district, buried in the text
- **Riders** — provisions completely unrelated to the bill's topic, attached because the main bill was likely to pass
- **Industry carve-outs** — regulatory exemptions or liability shields granted to specific companies or sectors
- **Agricultural subsidies** — payments to specific crops or farming industries (dairy, corn, ethanol, etc.)
- **Foreign benefits** — aid, loan guarantees, or trade advantages granted to specific countries or foreign entities
- **Sunset clauses** — provisions that expire after a few years, making a cost look smaller than it really is
- **Phase-in delays** — implementation timelines that push the actual effect years into the future

This is how a lot of real political deal-making happens. It's not illegal — it's just buried in legal language and rarely covered by news.

**What StrawPoll does:** Explicitly instructs Claude to look for these hidden provisions when summarizing bills and surface them in a dedicated section.

---

### Problem 4: There's no easy way to see the gap between votes and public opinion

Politicians constantly claim to represent their constituents. Polling data shows what people in each state actually think about issues like healthcare, immigration, climate policy, and more.

But nobody has built a tool that puts those two things next to each other:
- "Senator X voted to restrict Medicaid funding 8 out of 10 times on healthcare bills"
- "Meanwhile, 67% of voters in their state support expanding Medicaid access"

That gap is the most politically meaningful number in the entire app. It tells you whether a senator is actually representing the people who elected them, or whether they're serving someone else.

**What StrawPoll will do (Phase 3):** Pull state-level polling data on each of the 22 issue categories, compare it to each senator's voting record in that category, and surface the gap. No spin, no labels — just the comparison.

---

### Problem 5: The data exists but it's scattered and fragmented

Vote records, bill text, politician information, and polling data are all on different websites in different formats — Senate.gov XML, Congress.gov JSON, LegiScan API, Harvard Dataverse CSVs, polling firm websites. None of them talk to each other.

**What StrawPoll does:** Assembles all of this into one unified database with a single AI-powered query interface.

---

## How Each Feature Solves These Problems

| Feature | Problems It Solves |
|---|---|
| `seed_db.py` — imports all Senate votes | #1: Makes records queryable |
| `tag_bills.py` — categorizes bills by issue | #1, #4: Enables filtering by topic; needed for polling comparison |
| AI chat (`/chat` endpoint) | #1, #2: Natural language access to vote data |
| `summarize_bills.py` — Claude bill summaries | #2, #3: Plain English + hidden provisions section |
| Neutral scoring design (yea_action field) | #4: Factual record without political framing |
| Polling integration (Phase 3, not yet built) | #4: The vote-vs-opinion gap |
| Data unification (the whole DB) | #5: One place for all the data |

---

## What StrawPoll Is Not

- **Not a partisan tool.** The app doesn't label votes good or bad, progressive or conservative. It shows what senators voted for in concrete, factual terms and lets users draw their own conclusions.
- **Not a news site.** StrawPoll doesn't editorialize or interpret political events. It surfaces records.
- **Not a prediction tool.** It tracks what happened, not what will happen.
- **Not complete yet.** The polling comparison — which is the most powerful feature — hasn't been built yet. The current version is the data foundation it depends on.
