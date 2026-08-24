# UI Help Copy

All product tooltip and hover text is maintained in `dq-studio/src/data/helpContent.js`.

To change a tooltip, edit the relevant keyed entry in `helpContent`. UI components reference those keys through `InfoHint`, so text updates do not require changes in the page components unless a new tooltip key is being introduced.

When visible copy names an agent by call name, use the `Agent <CallName>` format, for example `Agent Pascal`.
