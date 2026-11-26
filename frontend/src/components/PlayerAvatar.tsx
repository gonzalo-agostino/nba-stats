import { useState } from "react";
import { playerHeadshotUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

interface PlayerAvatarProps {
  playerId: number;
  name: string;
  size?: "sm" | "md" | "lg";
  className?: string;
}

function getInitials(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

const SIZE_CLASSES: Record<"sm" | "md" | "lg", string> = {
  sm: "h-6 w-6 text-[9px]",
  md: "h-8 w-8 text-[11px]",
  lg: "h-16 w-16 text-lg",
};

export function PlayerAvatar({
  playerId,
  name,
  size = "sm",
  className,
}: PlayerAvatarProps) {
  const [failed, setFailed] = useState(false);

  return (
    <div
      className={cn(
        "relative shrink-0 overflow-hidden rounded-full border border-[var(--border-hairline)] bg-[var(--bg-subtle)] flex items-center justify-center font-semibold text-[var(--text-secondary)] select-none",
        SIZE_CLASSES[size],
        className
      )}
    >
      {!failed ? (
        <img
          src={playerHeadshotUrl(playerId)}
          alt={name}
          loading="lazy"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover object-top"
        />
      ) : (
        <span>{getInitials(name)}</span>
      )}
    </div>
  );
}
