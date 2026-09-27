/**
 * Lucide-named icon components drawn with Hugeicons, the project's one icon library.
 *
 * Registry components (assistant-ui) import their icons from "lucide-react". Pointing those imports
 * here keeps a single icon library without rewriting each component body, so a component re-added
 * from the registry needs only its import line changed. Hugeicons ships most Lucide names as
 * aliases; the two it lacks are mapped explicitly below.
 */
import type { ComponentProps } from "react";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import {
  AlertCircleIcon as AlertCircleSvg,
  ArchiveIcon as ArchiveSvg,
  ArrowDownIcon as ArrowDownSvg,
  ArrowUpIcon as ArrowUpSvg,
  AudioLinesIcon as AudioLinesSvg,
  BracesIcon as BracesSvg,
  BrainIcon as BrainSvg,
  CancelCircleIcon as CancelCircleSvg,
  CheckIcon as CheckSvg,
  ChevronDownIcon as ChevronDownSvg,
  ChevronLeftIcon as ChevronLeftSvg,
  ChevronRightIcon as ChevronRightSvg,
  CopyIcon as CopySvg,
  DownloadIcon as DownloadSvg,
  FileIcon as FileSvg,
  FileTextIcon as FileTextSvg,
  ImageIcon as ImageSvg,
  ImageOffIcon as ImageOffSvg,
  LoaderCircleIcon as LoaderCircleSvg,
  LoaderIcon as LoaderSvg,
  MicIcon as MicSvg,
  MoreHorizontalIcon as MoreHorizontalSvg,
  MusicIcon as MusicSvg,
  PencilIcon as PencilSvg,
  PhoneIcon as PhoneSvg,
  PlusIcon as PlusSvg,
  RefreshCwIcon as RefreshCwSvg,
  SearchIcon as SearchSvg,
  ShieldAlertIcon as ShieldAlertSvg,
  SquareIcon as SquareSvg,
  ThumbsDownIcon as ThumbsDownSvg,
  ThumbsUpIcon as ThumbsUpSvg,
  TrashIcon as TrashSvg,
  VideoIcon as VideoSvg,
  XIcon as XSvg,
} from "@hugeicons/core-free-icons";

export type IconProps = Omit<ComponentProps<typeof HugeiconsIcon>, "icon">;

/** One stroke width everywhere, matching the shadcn components' own icons. */
const STROKE_WIDTH = 2;

function icon(svg: IconSvgElement, name: string) {
  function Icon(props: IconProps) {
    return <HugeiconsIcon icon={svg} strokeWidth={STROKE_WIDTH} {...props} />;
  }
  Icon.displayName = name;
  return Icon;
}

export const AlertCircleIcon = icon(AlertCircleSvg, "AlertCircleIcon");
export const ArchiveIcon = icon(ArchiveSvg, "ArchiveIcon");
export const ArrowDownIcon = icon(ArrowDownSvg, "ArrowDownIcon");
export const ArrowUpIcon = icon(ArrowUpSvg, "ArrowUpIcon");
export const AudioLinesIcon = icon(AudioLinesSvg, "AudioLinesIcon");
export const BracesIcon = icon(BracesSvg, "BracesIcon");
export const BrainIcon = icon(BrainSvg, "BrainIcon");
export const CheckIcon = icon(CheckSvg, "CheckIcon");
export const ChevronDownIcon = icon(ChevronDownSvg, "ChevronDownIcon");
export const ChevronLeftIcon = icon(ChevronLeftSvg, "ChevronLeftIcon");
export const ChevronRightIcon = icon(ChevronRightSvg, "ChevronRightIcon");
export const CopyIcon = icon(CopySvg, "CopyIcon");
export const DownloadIcon = icon(DownloadSvg, "DownloadIcon");
export const FileIcon = icon(FileSvg, "FileIcon");
export const FileText = icon(FileTextSvg, "FileText");
export const FileTextIcon = icon(FileTextSvg, "FileTextIcon");
export const ImageIcon = icon(ImageSvg, "ImageIcon");
export const ImageOffIcon = icon(ImageOffSvg, "ImageOffIcon");
// Lucide renamed Loader2 to LoaderCircle; Hugeicons only has the new name.
export const Loader2Icon = icon(LoaderCircleSvg, "Loader2Icon");
export const LoaderIcon = icon(LoaderSvg, "LoaderIcon");
export const MicIcon = icon(MicSvg, "MicIcon");
export const MoreHorizontalIcon = icon(MoreHorizontalSvg, "MoreHorizontalIcon");
export const MusicIcon = icon(MusicSvg, "MusicIcon");
export const PencilIcon = icon(PencilSvg, "PencilIcon");
export const PhoneIcon = icon(PhoneSvg, "PhoneIcon");
export const PlusIcon = icon(PlusSvg, "PlusIcon");
export const RefreshCwIcon = icon(RefreshCwSvg, "RefreshCwIcon");
export const SearchIcon = icon(SearchSvg, "SearchIcon");
export const ShieldAlertIcon = icon(ShieldAlertSvg, "ShieldAlertIcon");
export const SquareIcon = icon(SquareSvg, "SquareIcon");
export const ThumbsDownIcon = icon(ThumbsDownSvg, "ThumbsDownIcon");
export const ThumbsUpIcon = icon(ThumbsUpSvg, "ThumbsUpIcon");
export const TrashIcon = icon(TrashSvg, "TrashIcon");
export const VideoIcon = icon(VideoSvg, "VideoIcon");
// Hugeicons has no XCircle alias with the Icon suffix; CancelCircle is the same glyph.
export const XCircleIcon = icon(CancelCircleSvg, "XCircleIcon");
export const XIcon = icon(XSvg, "XIcon");
