"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { CreateCaseDialog } from "@/components/create-case-dialog";
import { RenameProjectDialog } from "@/components/rename-project-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  FolderPlus,
  Loader2,
  LogOut,
  MessageSquare,
  MoreVertical,
  Pencil,
  Trash2,
} from "lucide-react";
import type { Project, SessionDocument } from "@/lib/types";

type ProjectState = Project & {
  documents: SessionDocument[];
};

type LocalChatSession = {
  id: string;
  title: string;
  projectId: string;
  createdAt: string;
};

interface CaseSelectionScreenProps {
  projects: ProjectState[];
  sessions: LocalChatSession[];
  isLoading: boolean;
  onSelectProject: (projectId: string) => void;
  onCreateProject: (name: string) => void;
  onRenameProject: (projectId: string, newName: string) => void;
  onDeleteProject: (projectId: string) => void;
  onSignOut: () => void;
}

export function CaseSelectionScreen({
  projects,
  sessions,
  isLoading,
  onSelectProject,
  onCreateProject,
  onRenameProject,
  onDeleteProject,
  onSignOut,
}: CaseSelectionScreenProps) {
  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [isRenameDialogOpen, setIsRenameDialogOpen] = useState(false);
  const [isDeleteAlertOpen, setIsDeleteAlertOpen] = useState(false);
  const [selectedProject, setSelectedProject] = useState<ProjectState | null>(null);

  const handleOpenDialog = () => setIsDialogOpen(true);

  const handleOpenRenameDialog = (project: ProjectState, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedProject(project);
    setIsRenameDialogOpen(true);
  };

  const handleOpenDeleteAlert = (project: ProjectState, e: React.MouseEvent) => {
    e.stopPropagation();
    setSelectedProject(project);
    setIsDeleteAlertOpen(true);
  };

  const handleRenameConfirm = (newName: string) => {
    if (selectedProject) {
      onRenameProject(selectedProject.id, newName);
    }
  };

  const handleDeleteConfirm = () => {
    if (selectedProject) {
      onDeleteProject(selectedProject.id);
      setIsDeleteAlertOpen(false);
      setSelectedProject(null);
    }
  };

  const getProjectStats = (projectId: string) => {
    const projectSessions = sessions.filter((s) => s.projectId === projectId);
    return { chatCount: projectSessions.length };
  };

  return (
    <div className="flex min-h-screen flex-col" style={{ background: "var(--bg)" }}>
      <header className="app-header">
        <div className="container-x app-header-inner">
          <h1
            className="logo"
            style={{ fontFamily: "var(--font-serif-family)", fontWeight: 600 }}
          >
            Джихелпер<span className="dot">.</span>
          </h1>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              onClick={onSignOut}
              title="Выйти"
              style={{ color: "var(--text-secondary)" }}
            >
              <LogOut className="h-5 w-5" />
              <span className="sr-only">Выйти</span>
            </Button>
          </div>
        </div>
      </header>

      <main className="flex-1">
        <div className="container-x" style={{ paddingTop: 48, paddingBottom: 96 }}>
          <div
            className="flex items-end justify-between gap-8 flex-wrap"
            style={{ marginBottom: 32 }}
          >
            <div>
              <h2
                style={{
                  fontSize: "clamp(32px, 4vw, 44px)",
                  margin: "0 0 6px",
                }}
              >
                Мои дела
              </h2>
              <p style={{ color: "var(--text-secondary)", fontSize: 16, margin: 0 }}>
                {isLoading
                  ? "Загрузка..."
                  : `${projects.length} ${
                      projects.length === 1
                        ? "дело"
                        : projects.length < 5
                        ? "дела"
                        : "дел"
                    }`}
              </p>
            </div>
            <Button onClick={handleOpenDialog} className="btn btn-primary gap-2 shrink-0">
              <FolderPlus className="h-5 w-5" />
              Новое дело
            </Button>
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center" style={{ padding: "48px 0" }}>
              <Loader2 className="h-8 w-8 animate-spin" style={{ color: "var(--text-secondary)" }} />
            </div>
          ) : projects.length === 0 ? (
            <div
              className="card-x flex flex-col items-center justify-center text-center"
              style={{
                padding: "64px 24px",
                borderStyle: "dashed",
                borderColor: "#c8c5b8",
              }}
            >
              <div
                style={{
                  width: 56,
                  height: 56,
                  borderRadius: "50%",
                  background: "var(--brand-accent-bg)",
                  display: "grid",
                  placeItems: "center",
                  color: "var(--brand-accent)",
                  marginBottom: 20,
                }}
              >
                <FolderPlus className="h-7 w-7" />
              </div>
              <h3 style={{ fontSize: 22, fontWeight: 500, margin: "0 0 8px" }}>
                У вас пока нет дел
              </h3>
              <p
                style={{
                  color: "var(--text-secondary)",
                  fontSize: 15,
                  maxWidth: 380,
                  margin: "0 0 24px",
                }}
              >
                Создайте первое дело, чтобы начать работу с AI-помощником
              </p>
              <Button onClick={handleOpenDialog} className="btn btn-primary gap-2">
                <FolderPlus className="h-4 w-4" />
                Создать первое дело
              </Button>
            </div>
          ) : (
            <ScrollArea className="h-[calc(100vh-260px)]">
              <div
                className="grid gap-5 auto-rows-fr"
                style={{ gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}
              >
                {projects.map((project) => {
                  const stats = getProjectStats(project.id);
                  const lastUpdate = new Date(project.updated_at ?? project.created_at);

                  return (
                    <div
                      key={project.id}
                      className="card-x is-clickable flex flex-col"
                      onClick={() => onSelectProject(project.id)}
                      style={{ minHeight: 180 }}
                    >
                      <div className="flex items-start justify-between gap-2" style={{ marginBottom: 12 }}>
                        <h3
                          style={{
                            fontSize: 20,
                            fontWeight: 500,
                            lineHeight: 1.25,
                            margin: 0,
                            wordBreak: "break-word",
                          }}
                        >
                          {project.name}
                        </h3>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-7 w-7 p-0 shrink-0"
                              onClick={(e) => e.stopPropagation()}
                              style={{ color: "var(--text-secondary)" }}
                            >
                              <MoreVertical className="h-4 w-4" />
                              <span className="sr-only">Открыть меню</span>
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={(e) => handleOpenRenameDialog(project, e)}>
                              <Pencil className="mr-2 h-4 w-4" />
                              Переименовать
                            </DropdownMenuItem>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem
                              onClick={(e) => handleOpenDeleteAlert(project, e)}
                              className="text-destructive focus:text-destructive"
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              Удалить
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>

                      <div
                        className="flex items-center gap-4 flex-wrap"
                        style={{
                          marginTop: "auto",
                          fontSize: 13,
                          color: "var(--text-secondary)",
                        }}
                      >
                        <span className="inline-flex items-center gap-1.5">
                          <MessageSquare className="h-3.5 w-3.5" />
                          {stats.chatCount}{" "}
                          {stats.chatCount === 1
                            ? "чат"
                            : stats.chatCount < 5
                            ? "чата"
                            : "чатов"}
                        </span>
                        <span>
                          обновлено {lastUpdate.toLocaleDateString("ru-RU")}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            </ScrollArea>
          )}
        </div>
      </main>

      <CreateCaseDialog
        open={isDialogOpen}
        onOpenChange={setIsDialogOpen}
        onConfirm={onCreateProject}
        defaultName={`Новое дело ${projects.length + 1}`}
      />

      <RenameProjectDialog
        open={isRenameDialogOpen}
        currentName={selectedProject?.name ?? ""}
        onOpenChange={setIsRenameDialogOpen}
        onConfirm={handleRenameConfirm}
      />

      <AlertDialog open={isDeleteAlertOpen} onOpenChange={setIsDeleteAlertOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Удалить дело?</AlertDialogTitle>
            <AlertDialogDescription>
              Это действие нельзя отменить. Проект &quot;{selectedProject?.name}&quot; и все его
              документы и чаты будут удалены навсегда.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Отмена</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteConfirm}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Удалить
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
