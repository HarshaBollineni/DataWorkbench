import { useState } from "react";
import { Check, Ticket } from "lucide-react";

import { approveRcaFix, confirmRcaFixApplied, proposeRcaFix } from "@/api/client";
import { Button } from "@/components/ui/button";

export default function FixApprovalFlow({ hypothesis, busy, run }) {
  const [proposalId, setProposalId] = useState(null);
  const [approvalId, setApprovalId] = useState(null);
  return <div className="flex flex-wrap items-center gap-2">
    {!proposalId && <Button size="sm" disabled={busy} onClick={async () => { const { proposal } = await proposeRcaFix(hypothesis.hypothesis_id); setProposalId(proposal.id); }}><Ticket className="h-4 w-4" /> Propose fix</Button>}
    {proposalId && !approvalId && <Button size="sm" disabled={busy} onClick={() => run(async () => { const response = await approveRcaFix(proposalId); setApprovalId(response.approval.id); })}><Check className="h-4 w-4" /> Human: approve fix</Button>}
    {approvalId && <Button size="sm" disabled={busy} onClick={() => run(() => confirmRcaFixApplied(approvalId))}><Check className="h-4 w-4" /> Confirm owner applied the fix (simulated)</Button>}
  </div>;
}
