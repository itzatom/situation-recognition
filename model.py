'''
PyTorch implementation of GGNN based SR : https://arxiv.org/abs/1708.04320
GGNN implementation adapted from https://github.com/chingyaoc/ggnn.pytorch
'''

import torch
import torch.nn as nn
import torchvision as tv

class vgg16_modified(nn.Module):
  def __init__(self):
    super(vgg16_modified, self).__init__()
    vgg = tv.models.vgg16_bn(pretrained=True)
    self.vgg_features = vgg.features
    self.out_features = vgg.classifier[6].in_features
    features = list(vgg.classifier.children())[:-1] # Remove last layer
    self.vgg_classifier = nn.Sequential(*features) # Replace the model classifier

    self.resize = nn.Sequential(
      nn.Linear(4096, 1024)
    )

  def forward(self,x):
    features = self.vgg_features(x)
    y = self.resize(self.vgg_classifier(features.view(-1, 512*7*7)))
    return y

class resnext_modified(nn.Module):
  def __init__(self):
    super(resnext_modified, self).__init__()
    self.resnext = tv.models.resnext101_32x8d(pretrained=True)
    self.resnext.fc = nn.Identity()

  def forward(self, x):
    return self.resnext(x)

class resnet_modified(nn.Module):
  def __init__(self):
    super(resnet_modified, self).__init__()
    self.resnet = tv.models.resnet152(pretrained=True)
    self.resnet.fc = nn.Identity()
  
  def forward(self, x):
    return self.resnet(x)

class GGSNN(nn.Module):
  """
  Gated Graph Sequence Neural Networks (GGNN)
  Mode: SelectNode
  Implementation based on https://arxiv.org/abs/1511.05493
  """
  def __init__(self, n_node, layersize):
    super(GGSNN, self).__init__()

    self.n_node = n_node
    #neighbour projection
    self.W_p = nn.Linear(layersize, layersize)
    #weights of update gate
    self.W_z = nn.Linear(layersize, layersize)
    self.U_z = nn.Linear(layersize, layersize)
    #weights of reset gate
    self.W_r = nn.Linear(layersize, layersize)
    self.U_r = nn.Linear(layersize, layersize)
    #weights of transform
    self.W_h = nn.Linear(layersize, layersize)
    self.U_h = nn.Linear(layersize, layersize)

  def forward(self, init_node, mask):

    hidden_state = init_node
    for t in range(4):
      # calculating neighbour info
      neighbours = hidden_state.contiguous().view(mask.size(0), self.n_node, -1)
      neighbours = neighbours.expand(self.n_node, neighbours.size(0), neighbours.size(1), neighbours.size(2))
      neighbours = neighbours.transpose(0,1)

      neighbours = neighbours * mask.unsqueeze(-1)
      neighbours = self.W_p(neighbours)
      neighbours = torch.sum(neighbours, 2)
      neighbours = neighbours.contiguous().view(mask.size(0)*self.n_node, -1)

      #applying gating
      z_t = torch.sigmoid(self.W_z(neighbours) + self.U_z(hidden_state))
      r_t = torch.sigmoid(self.W_r(neighbours) + self.U_r(hidden_state))
      h_hat_t = torch.tanh(self.W_h(neighbours) + self.U_h(r_t * hidden_state))
      hidden_state = (1 - z_t) * hidden_state + z_t * h_hat_t

    return hidden_state

class FCGGNN(nn.Module):
  def __init__(self, encoder, D_hidden_state):
    super(FCGGNN, self).__init__()
    self.encoder = encoder
    
    self.role_emb = nn.Embedding(encoder.get_num_roles()+1, D_hidden_state, padding_idx=encoder.get_num_roles())
    self.verb_emb = nn.Embedding(encoder.get_num_verbs(), D_hidden_state)

    self.convnet = resnet_modified()
    self.role_emb = role_emb
    self.verb_emb = verb_emb
    self.ggsnn = GGSNN(n_node=encoder.max_role_count, layersize=D_hidden_state)
    
    self.gt_nouns_classifier = nn.Sequential(
      nn.Dropout(0.5),
      nn.Linear(D_hidden_state, encoder.get_num_labels())
    )

    self.verb_classifier = nn.Sequential(
      nn.Dropout(0.5),
      nn.Linear(D_hidden_state, encoder.get_num_labels())
    )

    self.nouns_classifier = nn.Sequential(
      nn.Dropout(0.5),
      nn.Linear(D_hidden_state, encoder.get_num_labels())
    )

    self.non_linear = nn.Sequential(
      nn.ReLU(2048, encoder.get_num_labels())
    )

  def _predict_noun_with_gtverb(self, img_features, gt_verb, batch_size):

    role_idx = self.encoder.get_role_ids_batch(gt_verb)

    role_idx = role_idx.cuda()

    # repeat single image for max role count a frame can have
    img_features = img_features.expand(self.encoder.max_role_count, img_features.size(0), img_features.size(1))

    img_features = img_features.transpose(0,1)
    img_features = img_features.contiguous().view(batch_size * self.encoder.max_role_count, -1)

    verb_embd = self.verb_emb(gt_verb)
    role_embd = self.role_emb(role_idx)

    role_embd = role_embd.view(batch_size * self.encoder.max_role_count, -1)

    verb_embed_expand = verb_embd.expand(self.encoder.max_role_count, verb_embd.size(0), verb_embd.size(1))
    verb_embed_expand = verb_embed_expand.transpose(0,1)
    verb_embed_expand = verb_embed_expand.contiguous().view(batch_size * self.encoder.max_role_count, -1)

    input2ggnn = self.non_linear(img_features * role_embd * verb_embed_expand)

    #mask out non exisiting roles from max role count a frame can have
    mask = self.encoder.get_adj_matrix_noself(gt_verb)
    mask = mask.cuda()

    out = self.ggsnn(input2ggnn, mask)
    logits = self.gt_nouns_classifier(out)
    # return predicted nouns based on grount truth of images in batch
    return logits.contiguous().view(batch_size, self.encoder.max_role_count, -1)


  def _predict_verb(self, img_features, batch_size):
    mask = torch.tensor()
    mask.cuda()
    out = self.ggsnn(img_features, mask)
    logits = self.gt_nouns_classifier(out)
    # return predicted verb based on images in batch
    return logits.contiguous().view(batch_size, 1, -1)


  def _predict_nouns(self, img_features, pred_verb, batch_size):
    mask = torch.tensor()
    mask.cuda()
    out = self.ggsnn(img_features, mask)
    logits = self.gt_nouns_classifier(out)
    # return predicted nouns based on images in batch
    return logits.contiguous().view(batch_size, self.encoder.max_role_count, -1)


  def forward(self, img, gt_verb):
    img_features = self.convnet(img)
    batch_size = img_features.size(0)
    gt_pred_nouns = self._predict_noun_with_gtverb(img_features, gt_verb, batch_size)
    pred_verb = self._predict_verb(img_features, batch_size)
    pred_nouns = self._predict_nouns(img_features, pred_verb, batch_size)
    return pred_verb, pred_nouns, gt_pred_nouns


  def calculate_loss(self, gt_verbs, role_label_pred, gt_labels):
    batch_size = role_label_pred.size()[0]
    criterion = nn.CrossEntropyLoss(ignore_index=self.encoder.get_num_labels())

    gt_label_turned = gt_labels.transpose(1,2).contiguous().view(batch_size* self.encoder.max_role_count*3, -1)

    role_label_pred = role_label_pred.contiguous().view(batch_size* self.encoder.max_role_count, -1)
    role_label_pred = role_label_pred.expand(3, role_label_pred.size(0), role_label_pred.size(1))
    role_label_pred = role_label_pred.transpose(0,1)
    role_label_pred = role_label_pred.contiguous().view(-1, role_label_pred.size(-1))

    return criterion(role_label_pred, gt_label_turned.squeeze(1)) * 3