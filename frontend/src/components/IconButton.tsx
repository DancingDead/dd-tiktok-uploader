import { Button } from "@/components/ui/button"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"

type Props = React.ComponentProps<typeof Button> & { tip: string }

// Bouton-icône (ghost/icon par défaut) avec tooltip — remplace les `title=""`.
export function IconButton({ tip, variant = "ghost", size = "icon", children, ...props }: Props) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant={variant} size={size} {...props}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{tip}</TooltipContent>
    </Tooltip>
  )
}
