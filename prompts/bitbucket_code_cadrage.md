Le code du Standard ET celui du dépôt client se lisent UNIQUEMENT dans Bitbucket, à jour, avec les deux commandes ci-dessous — jamais dans une copie locale (aucune n'est préparée pour le cadrage) :
- Socle Standard (projet ${std_project}, ${std_ref_label}) : ${std_cli}
- Dépôt client (projet ${client_project}, ${client_repos}, branche integration) : ${client_cli}
Sous-commandes, identiques pour les deux (CLI = l'une des deux commandes ci-dessus) :
  CLI repos --like TEXTE                       dépôts lisibles
  CLI search "TEXTE"                           recherche de code (index de la branche par défaut seulement : confirme chaque résultat par show ou grep)
  CLI files DEPOT --prefix DOSSIER/ --like TEXTE   fichiers d'un dépôt
  CLI show DEPOT CHEMIN --from N --to M        contenu d'un fichier (numéros de ligne)
  CLI grep DEPOT "REGEX" --prefix DOSSIER/ --ext .java   occurrences dans un dépôt
Lance chaque commande seule, telle quelle (sans cd, pipe, && ni redirection). Une ligne BITBUCKET_ERROR=… signale un accès impossible : dis-le dans le cadrage, sans supposer le code.
Cadrage : aucun checkout, aucune copie locale, aucune modification de code.
