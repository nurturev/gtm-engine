---
name: humanizer
description: Strips AI-generated tone from written content (cold emails, LinkedIn posts, Slack messages, social copy) so it reads as human-written. Use when the user asks to "humanize", "make it sound human", "remove AI tone", or says copy sounds "too corporate", "too polished", or "too AI".
---

# Content Humanizer

Strip AI-generated tone from any written content. Make it sound like a real person wrote it — not a language model.

## When to Activate

Trigger when:
- User asks to "humanize", "make it sound human", "remove AI tone", "make it natural"
- Writing LinkedIn posts, cold emails, social media captions, Slack messages
- Any content that will be published or sent to real people
- User says "this sounds too AI", "too corporate", "too polished"

## The Problem with AI Writing

AI-generated text has telltale patterns that people instinctively detect:
- **Excessive structure** — numbered lists, headers, bullet points for everything
- **Hollow filler phrases** — "In today's fast-paced world", "It's worth noting that", "Let's dive in"
- **Over-hedging** — "It's important to consider", "One might argue", "There are several factors"
- **Perfect parallelism** — every sentence balanced, every list item same length
- **Emoji abuse** — 🚀🔥💡🎯 scattered everywhere as fake enthusiasm
- **Corporate buzzwords** — "leverage", "synergy", "game-changer", "unlock", "transform"
- **Sycophantic openers** — "Great question!", "Absolutely!", "That's a fantastic point!"
- **False enthusiasm** — everything is "exciting", "incredible", "powerful"

## Humanizer Rules (apply ALL of these)

### 1. Sentence Variety
- Mix short punchy sentences with longer ones
- Start some sentences with "And", "But", "So" — real people do this
- Use fragments. Like this. It's fine.
- Don't make every sentence the same length

### 2. Kill the Filler
Delete these phrases entirely:
- "In today's [anything]"
- "It's worth noting/mentioning"
- "Let's dive in/unpack/explore"
- "At the end of the day"
- "The reality is"
- "Here's the thing"
- "I'm excited to share"
- "Thrilled to announce"

### 3. Be Specific, Not Vague
- BAD: "We saw incredible results with our new approach"
- GOOD: "Reply rates went from 2% to 11% after we switched to the new subject lines"

### 4. Imperfect is Human
- It's OK to start a paragraph mid-thought
- Parenthetical asides are natural (like this one)
- Dashes work better than semicolons — they feel more casual
- ONE emoji max per post, and only if it fits naturally

### 5. Voice Matching
Before humanizing, check:
- Is this a LinkedIn post? → professional but conversational, personal anecdotes welcome
- Cold email? → ultra short, one question, no fluff, sounds like a busy person typed it fast
- Slack message? → casual, lowercase ok, fragments ok
- Social media? → punchy, opinionated, contrarian angles work

### 6. LinkedIn-Specific
- First line is the hook — make it interrupt the scroll
- No "I'm thrilled to announce" — just state the thing
- Personal stories > generic advice
- Hot takes > safe platitudes
- End with a question or call-to-action, never a generic "What do you think?"
- Whitespace matters — short paragraphs, line breaks between thoughts

### 7. Cold Email-Specific
- Subject line: 3-5 words, lowercase, looks like a real email
- Body: 2-3 sentences max. One clear ask.
- NO "I hope this email finds you well"
- NO "I came across your profile and was impressed"
- Sound like you're busy and this is worth their time
- Sign off casually: "- [name]" not "Best regards,"

### 8. The Final Test
Read the content out loud. If you wouldn't say it in a real conversation, rewrite it.
Would a person actually post this on LinkedIn? Or does it scream "I had ChatGPT write this"?

## Example Transformations

### LinkedIn Post
**AI version:**
"I'm excited to share that we've launched our new product! 🚀 In today's competitive landscape, it's more important than ever to leverage cutting-edge solutions. Here are 5 key takeaways from our journey: 1. Innovation is key..."

**Humanized:**
"We shipped the thing. Took 6 months longer than planned (classic). But the early numbers are wild — 3x the engagement we expected on day one. Biggest lesson? We almost killed the feature that's now driving 60% of signups."

### Cold Email
**AI version:**
"Dear [Name], I hope this message finds you well! I recently came across your impressive profile on LinkedIn and noticed your work in GTM engineering. I believe our solution could significantly enhance your team's productivity..."

**Humanized:**
"hey [name] — saw your post about replacing your SDR stack with Clay + n8n. we built something similar but skipped Clay entirely. worth a 10 min chat? - [sender]"
