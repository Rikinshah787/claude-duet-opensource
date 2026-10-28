'use client';

import { useMutation, useQueryClient, type UseMutationResult } from '@tanstack/react-query';
import { duet, qk } from './api';
import type { Approval, ApprovalActionResult } from './types';

/** Everything an approval decision can change, invalidated in one go. */
function useFanoutInvalidate(): () => void {
  const queryClient = useQueryClient();
  return () => {
    for (const key of [qk.thread, qk.me, qk.tasks, qk.audit, qk.auditVerify, qk.briefing, qk.signals]) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
    void queryClient.invalidateQueries({ queryKey: ['approvals'] });
    void queryClient.invalidateQueries({ queryKey: ['task'] });
  };
}

export type ApprovalDecision = { id: string; note?: string };

export type ApprovalActions = {
  approve: UseMutationResult<ApprovalActionResult, Error, ApprovalDecision>;
  reject: UseMutationResult<{ approval: Approval }, Error, ApprovalDecision>;
  revert: UseMutationResult<{ approval: Approval }, Error, { id: string }>;
  busy: boolean;
};

export function useApprovalActions(): ApprovalActions {
  const invalidate = useFanoutInvalidate();

  const approve = useMutation<ApprovalActionResult, Error, ApprovalDecision>({
    mutationFn: ({ id, note }) => duet.approve(id, note),
    onSuccess: invalidate,
  });

  const reject = useMutation<{ approval: Approval }, Error, ApprovalDecision>({
    mutationFn: ({ id, note }) => duet.reject(id, note),
    onSuccess: invalidate,
  });

  const revert = useMutation<{ approval: Approval }, Error, { id: string }>({
    mutationFn: ({ id }) => duet.revert(id),
    onSuccess: invalidate,
  });

  return {
    approve,
    reject,
    revert,
    busy: approve.isPending || reject.isPending || revert.isPending,
  };
}
