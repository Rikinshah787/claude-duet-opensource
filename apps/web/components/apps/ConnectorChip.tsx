import { BrandGlyph } from '@/components/apps/BrandMark';
import { Chip } from '@/components/ui/Chip';
import { getBrand } from '@/lib/brand';

/**
 * The "which app is this" chip, used on briefing rows, signal rows and
 * approval cards. Carries the real brand mark where there is one; where there
 * isn't, it is exactly the text chip it has always been.
 */
export function ConnectorChip({
  connectorKey,
  className,
}: {
  connectorKey: string | null | undefined;
  className?: string;
}) {
  if (!connectorKey) return null;
  const brand = getBrand(connectorKey);
  return (
    <Chip className={className} title={brand?.title ?? connectorKey}>
      <BrandGlyph connectorKey={connectorKey} className="h-3 w-3" />
      {brand?.title ?? connectorKey.replace(/_/g, ' ')}
    </Chip>
  );
}
