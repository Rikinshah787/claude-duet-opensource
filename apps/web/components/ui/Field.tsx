'use client';

import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from 'react';
import { cn } from '@/lib/cn';

const CONTROL =
  'w-full rounded-lg border border-hairline bg-raised px-3 py-2 text-sm text-ink placeholder:text-ink-faint transition-colors hover:border-hairline-strong';

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: (id: string) => ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={id} className="text-xs uppercase tracking-wider text-ink-faint">
        {label}
      </label>
      {children(id)}
      {hint ? <p className="text-xs text-ink-faint">{hint}</p> : null}
    </div>
  );
}

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  hint?: string;
  wrapperClassName?: string;
}

export function TextField({ label, hint, wrapperClassName, className, ...rest }: TextFieldProps) {
  return (
    <Field label={label} hint={hint} className={wrapperClassName}>
      {(id) => <input id={id} className={cn(CONTROL, className)} {...rest} />}
    </Field>
  );
}

export interface SelectFieldProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label: string;
  hint?: string;
  wrapperClassName?: string;
}

export function SelectField({ label, hint, wrapperClassName, className, children, ...rest }: SelectFieldProps) {
  return (
    <Field label={label} hint={hint} className={wrapperClassName}>
      {(id) => (
        <select id={id} className={cn(CONTROL, 'appearance-none pr-8', className)} {...rest}>
          {children}
        </select>
      )}
    </Field>
  );
}

export { CONTROL as controlClass };
