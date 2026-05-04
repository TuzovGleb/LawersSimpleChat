"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThemeToggle } from "@/components/theme-toggle";
import { ThemeProvider } from "@/components/theme-provider";
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
import { Bot, FolderPlus, Loader2, LogOut, MessageSquare, MoreVertical, Pencil, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
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

  const handleOpenDialog = () => {
    setIsDialogOpen(true);
  };

  const handleCreateProject = (name: string) => {
    onCreateProject(name);
  };

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
    const chatCount = projectSessions.length;

    return { chatCount };
  };

  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="light"
      enableSystem
      disableTransitionOnChange
    >
      <div className="flex min-h-screen flex-col bg-background" style={{ background: '#fafaf5' }}>
        <header className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60" style={{ background: '#fafaf5', borderBottom: '1px solid #982525' }}>
          <div className="flex h-16 items-center justify-between" style={{ paddingLeft: '2rem', paddingRight: '2rem' }}>
            <div className="flex items-center gap-3">
              <Bot className="h-6 w-6" style={{ color: '#982525' }} />
              <h1 className="text-xl font-bold" style={{ fontFamily: "'Courier New', 'Monaco', monospace", textTransform: 'uppercase' }}>МОИ ДЕЛА</h1>
            </div>
            <div className="flex items-center gap-2">
              <ThemeToggle />
              <Button
                variant="ghost"
                size="icon"
                onClick={onSignOut}
                title="Выйти"
              >
                <LogOut className="h-5 w-5" />
                <span className="sr-only">Выйти</span>
              </Button>
            </div>
          </div>
        </header>

      <main className="flex-1 py-8" style={{ paddingLeft: '2rem', paddingRight: '2rem' }}>
        <div>
          <div className="mb-8 flex items-start justify-between gap-4 mt-8">
            <div className="flex-1"></div>
            <Button onClick={handleOpenDialog} size="lg" variant="secondary" className="gap-2 shrink-0" style={{ background: '#982525', color: '#fff', border: '1px solid #000', fontFamily: "'Courier New', 'Monaco', monospace", fontWeight: 'bold', textTransform: 'uppercase' }}>
              <FolderPlus className="h-5 w-5" />
              Создать новое дело
            </Button>
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : projects.length === 0 ? (
            <Card className="border-dashed" style={{ border: '1px dashed #000', background: '#fafaf5' }}>
              <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                <div className="rounded-full bg-muted p-6" style={{ background: '#f0f0eb' }}>
                  <FolderPlus className="h-12 w-12 text-muted-foreground" style={{ color: '#982525' }} />
                </div>
                <h3 className="mt-6 text-xl font-semibold" style={{ fontFamily: "'Courier New', 'Monaco', monospace", textTransform: 'uppercase' }}>Нет дел</h3>
                <p className="mt-2 max-w-md text-sm text-muted-foreground" style={{ color: '#666', fontFamily: "'Courier New', 'Monaco', monospace" }}>
                  Создайте первое дело, чтобы начать общаться с AI-помощником
                </p>
                <Button onClick={handleOpenDialog} variant="secondary" className="mt-6 gap-2" style={{ background: '#982525', color: '#fff', border: '1px solid #000', fontFamily: "'Courier New', 'Monaco', monospace", fontWeight: 'bold', textTransform: 'uppercase' }}>
                  <FolderPlus className="h-4 w-4" />
                  Создать первое дело
                </Button>
              </CardContent>
            </Card>
          ) : (
            <ScrollArea className="h-[calc(100vh-280px)]">
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 auto-rows-fr">
                {projects.map((project) => {
                  const stats = getProjectStats(project.id);
                  const lastUpdate = new Date(project.updated_at ?? project.created_at);

                  return (
                    <Card
                      key={project.id}
                      className={cn(
                        "group cursor-pointer transition-all hover:border-foreground/20 hover:shadow-md flex flex-col",
                      )}
                      onClick={() => onSelectProject(project.id)}
                      style={{ border: '1px solid #000', background: '#fafaf5' }}
                    >
                      <CardContent className="p-6 flex flex-col flex-1">
                        <div className="mb-4 flex items-center justify-between gap-2">
                          <div className="rounded-lg bg-muted p-2 text-muted-foreground shrink-0" style={{ background: '#f0f0eb' }}>
                            <Bot className="h-5 w-5" style={{ color: '#982525' }} />
                          </div>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-8 w-8 p-0 shrink-0"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <MoreVertical className="h-4 w-4" />
                                <span className="sr-only">Открыть меню</span>
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent align="end">
                              <DropdownMenuItem
                                onClick={(e) => handleOpenRenameDialog(project, e)}
                              >
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
                        <h3 className="mb-3 text-lg font-semibold leading-tight" style={{ fontFamily: "'Courier New', 'Monaco', monospace", textTransform: 'uppercase' }}>
                          {project.name}
                        </h3>
                        <div className="space-y-2 text-sm text-muted-foreground flex-1" style={{ color: '#666', fontFamily: "'Courier New', 'Monaco', monospace" }}>
                          <div className="flex items-center gap-2">
                            <MessageSquare className="h-4 w-4 shrink-0" style={{ color: '#982525' }} />
                            <span>
                              {stats.chatCount}{" "}
                              {stats.chatCount === 1
                                ? "чат"
                                : stats.chatCount < 5
                                ? "чата"
                                : "чатов"}
                            </span>
                          </div>
                          <div className="mt-auto pt-3 border-t">
                            <span className="text-xs" style={{ borderTopColor: '#000' }}>
                              Обновлено: {lastUpdate.toLocaleDateString("ru-RU")}
                            </span>
                          </div>
                        </div>
                      </CardContent>
                    </Card>
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
        onConfirm={handleCreateProject}
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
            <AlertDialogTitle>Вы уверены?</AlertDialogTitle>
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
    </ThemeProvider>
  );
}

